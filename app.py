import os
import uuid # For generating unique URIs
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify # Added jsonify
from rdflib import Graph, Literal, Namespace, URIRef, BNode # Added BNode just in case, though UUID is better
from rdflib.namespace import RDF, RDFS, OWL, XSD # Import common namespaces and XSD for literals
from rdflib.store import Store
from rdflib.plugin import PluginException
from sqlalchemy.exc import OperationalError # To catch DB connection errors
import requests
from urllib.parse import quote_plus # For sanitizing names in URIs if not using UUIDs

# --- Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'ALT-Lappen')

# Get Database URL from environment variable. Fallback to SQLite for easy local dev.
# IMPORTANT: Use a persistent PostgreSQL URL for deployment (e.g., from Render, Neon, Supabase)
# Example PostgreSQL URL format: postgresql://user:password@host:port/database
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://neondb_owner:npg_l5ywsD3mCBGH@ep-square-sun-a29ca8p3-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require')

# Replace the local file path with the RAW GitHub URL
# Make sure this URL points to the *raw* content of your ontology file
# Example: Replace with your actual raw URL
ONTOLOGY_URL = 'https://raw.githubusercontent.com/Strubbelig/Flap-ontology-project/flap_onto_corrected_7.owl'
# --- RDFLib Setup ---
EX = Namespace("https://purl.bioontology.org/ontology/ONTOFLAP/")
BASE_URI = Namespace("https://purl.bioontology.org/ontology/ONTOFLAP/instances/") # Separate namespace for instances

graph_identifier = URIRef("urn:example:knowledge_graph")
store = None
g = None

# Helper function to get labels or fallback to local name
def get_label(subject_uri, graph):
    """Fetches rdfs:label for a URI, falling back to local name."""
    if isinstance(subject_uri, Literal):
        return str(subject_uri) # Return literal value directly
    try:
        label = graph.value(subject=subject_uri, predicate=RDFS.label)
        if label:
            return str(label)
        # Fallback: Try to extract local name after # or /
        name = str(subject_uri).split('#')[-1].split('/')[-1]
        return name if name else str(subject_uri)
    except Exception:
        return str(subject_uri) # Final fallback

def get_graph():
    """Initializes and returns the RDFLib Graph object."""
    global store, g
    if g is None:
        print(f"Connecting to RDF store at: {DATABASE_URL}")
        try:
            from rdflib.plugin import register, Plugin
            from rdflib.store import Store
            register('SQLAlchemy', Store, 'rdflib_sqlalchemy.store', 'SQLAlchemy')
            store = Graph(store='SQLAlchemy', identifier=graph_identifier)
            store.open(DATABASE_URL, create=True)
            g = store

            g.bind("ex", EX)
            g.bind("base", BASE_URI) # Bind instance base
            g.bind("rdf", RDF)
            g.bind("rdfs", RDFS)
            g.bind("owl", OWL)
            g.bind("xsd", XSD)

            # Check if ontology is loaded (simple check: see if a known class exists)
            # Use a class likely defined in your ontology
            ontology_marker_class = EX.OFLID10002 # Or another core class from ONTOFLAP
            if (ontology_marker_class, RDF.type, OWL.Class) not in g:
                print(f"Ontology marker class <{ontology_marker_class}> not found. Parsing from {ONTOLOGY_URL}...")
                try:
                    # Use a specific format if known (e.g., 'xml', 'turtle', 'json-ld')
                    # OWL often uses RDF/XML ('xml') or Turtle ('turtle')
                    # Try common formats or inspect the file/URL headers
                    g.parse(ONTOLOGY_URL, format="xml") # Try RDF/XML first for .owl
                    print(f"Ontology parsed successfully from URL (format: xml).")
                    # Check again if marker class exists after parsing
                    if (ontology_marker_class, RDF.type, OWL.Class) not in g:
                         print(f"WARNING: Ontology marker class <{ontology_marker_class}> still not found after parsing. Check the ontology file and namespace.")

                except requests.exceptions.RequestException as e:
                     print(f"ERROR: Failed to fetch ontology from {ONTOLOGY_URL}: {e}")
                     g = None # Failed, set g back to None
                except Exception as e:
                    print(f"ERROR: Failed to parse ontology (tried format: xml) from {ONTOLOGY_URL}: {e}")
                    # Optionally try another format like turtle
                    # try:
                    #     g.parse(ONTOLOGY_URL, format="turtle")
                    #     print(f"Ontology parsed successfully from URL (format: turtle).")
                    # except Exception as e2:
                    #      print(f"ERROR: Also failed to parse ontology as turtle: {e2}")
                    #      g = None # Failed both, set g back to None
                    g = None # Failed, set g back to None
            else:
                print("Ontology already loaded in the store.")

        except PluginException as e:
            print(f"ERROR: RDFLib store plugin issue: {e}")
            g = None
        except OperationalError as e:
            print(f"ERROR: Database connection failed: {e}")
            g = None
        except Exception as e:
            print(f"ERROR: An unexpected error occurred during graph initialization: {e}")
            g = None
    return g

# --- Helper Functions for Ontology/Graph Queries ---

def get_ontology_classes(graph):
    """Queries the graph for all OWL classes."""
    if graph is None: return []
    # Query for things explicitly declared as owl:Class
    # Or things that are the subject of an rdfs:subClassOf triple (broader)
    # Or things used as the range of rdfs:domain (might catch implicitly defined classes)
    # *** CHANGE: Renamed SPARQL variable from ?class to ?class_uri ***
    query = """
        SELECT DISTINCT ?class_uri ?label WHERE {
            { ?class_uri a owl:Class . }
            UNION
            { ?class_uri rdfs:subClassOf ?super . FILTER (!isBlank(?class_uri)) }
            UNION
            { ?prop rdfs:domain ?class_uri . FILTER (!isBlank(?class_uri)) }

            FILTER (!isBlank(?class_uri)) # Exclude blank nodes
            FILTER (!regex(str(?class_uri), "^http://www.w3.org/")) # Exclude RDF, RDFS, OWL, XSD built-ins

            # *** CHANGE: Use ?class_uri here too ***
            OPTIONAL { ?class_uri rdfs:label ?label . FILTER(lang(?label) = "" || langMatches(lang(?label), "en")) }
        } ORDER BY ?label ?class_uri
    """
    results = []
    try:
        for row in graph.query(query):
            # *** CHANGE: Access row.class_uri instead of row.class ***
            class_uri_ref = row.class_uri # Get the URIRef object from the row
            class_uri_str = str(class_uri_ref) # Convert to string

            # *** CHANGE: Use class_uri_ref (the URIRef) for get_label ***
            label = str(row.label) if row.label else get_label(class_uri_ref, graph) # Fallback label

            results.append({'uri': class_uri_str, 'label': label})
    except Exception as e:
        print(f"Error querying classes: {e}")
        # Optionally re-raise or log traceback for more detail
        # import traceback
        # print(traceback.format_exc())
    return results

def get_properties_for_class(graph, class_uri_str):
    """
    Queries for properties relevant to a class (via rdfs:domain).
    Includes properties without a domain specified.
    """
    if graph is None or not class_uri_str: return []
    class_uri = URIRef(class_uri_str)

    # SPARQL query to find properties:
    # 1. Properties explicitly having the class (or its superclasses) in their rdfs:domain.
    # 2. Properties declared as owl:ObjectProperty or owl:DatatypeProperty without a specific domain.
    # We need transitive subClassOf reasoning for domain matching which SPARQL 1.1 supports.
    query = """
        SELECT DISTINCT ?prop ?label ?range ?type WHERE {
            # Get superclasses of the given class (including itself)
            BIND(<%s> AS ?targetClass)
            ?targetClass rdfs:subClassOf* ?domainClass .

            {
                # Properties with matching domain
                ?prop rdfs:domain ?domainClass .
            } UNION {
                # Properties without a domain (potentially applicable)
                ?prop a ?propType .
                FILTER (?propType IN (owl:ObjectProperty, owl:DatatypeProperty, rdf:Property))
                FILTER NOT EXISTS { ?prop rdfs:domain ?anyDomain . }
            }

            # Ensure it's a property and filter out built-ins
            ?prop a ?propType .
            FILTER (?propType IN (owl:ObjectProperty, owl:DatatypeProperty, rdf:Property))
            FILTER (!regex(str(?prop), "^http://www.w3.org/"))

            OPTIONAL { ?prop rdfs:label ?l . FILTER(lang(?l) = "" || langMatches(lang(?l), "en")) }
            OPTIONAL { ?prop rdfs:range ?r . } # Get range if specified

            BIND(COALESCE(?l, REPLACE(REPLACE(str(?prop), "^.*/", ""), "^.*#", "")) AS ?label) # Calculate label fallback
            BIND(COALESCE(?r, "") AS ?range) # Use empty string if no range
            BIND(IF(?propType = owl:ObjectProperty, "object", IF(?propType = owl:DatatypeProperty, "datatype", "unknown")) AS ?type) # Determine type
        } ORDER BY ?label ?prop
        """ % class_uri_str # Inject the class URI into the query

    results = []
    try:
        print(f"Querying properties for class: {class_uri_str}")
        # print(f"Query:\n{query}") # Uncomment for debugging query
        qres = graph.query(query)
        # print(f"Found {len(qres)} potential properties.") # Debugging
        for row in qres:
            prop_uri = str(row.prop)
            label = str(row.label) # Already has fallback in query
            range_uri = str(row.range) if row.range else None
            prop_type = str(row.type) # 'object', 'datatype', or 'unknown'

            # Determine if range suggests literal or instance
            object_kind = "literal" # Default
            if prop_type == "object":
                object_kind = "instance"
            elif range_uri:
                 # If range is an OWL Class, expect an instance
                 # Check if range URI exists as a class in the graph
                 if (URIRef(range_uri), RDF.type, OWL.Class) in graph or \
                    (URIRef(range_uri), RDFS.subClassOf, None) in graph:
                     object_kind = "instance"
                 # Check common XSD types for literals
                 elif range_uri.startswith(str(XSD)):
                     object_kind = "literal"
            # If unknown type and no range, guess literal? Or provide choice? Let's default to literal.

            results.append({
                'uri': prop_uri,
                'label': label,
                'range': range_uri, # Keep range info if needed later
                'object_kind': object_kind # Indicates if object should be 'literal' or 'instance'
            })
        # Deduplicate based on URI (SPARQL DISTINCT should handle most, but UNION might reintroduce)
        seen_uris = set()
        unique_results = []
        for item in results:
            if item['uri'] not in seen_uris:
                unique_results.append(item)
                seen_uris.add(item['uri'])
        # print(f"Unique properties: {unique_results}") # Debugging
        return unique_results

    except Exception as e:
        print(f"Error querying properties for class {class_uri_str}: {e}")
    return []


def get_existing_instances(graph, class_uri_str=None):
    """Queries for existing instances, optionally filtered by class."""
    if graph is None: return []
    query = """
        SELECT DISTINCT ?instance ?label WHERE {
            ?instance a ?type .
            %s
            # Try to exclude classes/properties themselves unless explicitly typed otherwise
            FILTER NOT EXISTS { ?instance a owl:Class . }
            FILTER NOT EXISTS { ?instance a rdf:Property . }
            FILTER (!isBlank(?instance)) # No blank nodes

            # Get label preferentially, fallback to extracting from URI
             OPTIONAL { ?instance rdfs:label ?l . FILTER(lang(?l) = "" || langMatches(lang(?l), "en")) }
             BIND(COALESCE(?l, REPLACE(REPLACE(str(?instance), "^.*/", ""), "^.*#", "")) AS ?label)
        } ORDER BY ?label ?instance
        """
    # Add class filtering if provided
    class_filter = ""
    if class_uri_str:
        # Include instances whose type is the given class or a subclass thereof
        class_filter = "?instance a <%s> . \n ?type rdfs:subClassOf* <%s> ." % (class_uri_str, class_uri_str)
        # Simpler version: just direct type match
        # class_filter = "?instance a <%s> ." % class_uri_str
        query = query % ("?instance a ?type .\n ?type rdfs:subClassOf* <%s> ." % class_uri_str) # Use subClassOf*
    else:
        query = query % ("") # No class filter

    results = []
    try:
        for row in graph.query(query):
            instance_uri = str(row.instance)
            # Filter out ontology base URI itself if it appears
            if instance_uri == str(EX):
                 continue
            label = str(row.label) if row.label else get_label(row.instance, graph) # Fallback
            results.append({'uri': instance_uri, 'label': label})
    except Exception as e:
        print(f"Error querying instances (class: {class_uri_str}): {e}")
    return results


def mint_instance_uri(base_namespace, name=None):
    """Creates a unique URI for a new instance using UUID."""
    # Using UUID is generally safer and avoids collisions/escaping issues
    unique_id = str(uuid.uuid4())
    # Optionally include a sanitized name for readability, but UUID ensures uniqueness
    # if name:
    #     sanitized_name = quote_plus(name.lower().replace(" ", "_"))
    #     return base_namespace[f"{sanitized_name}_{unique_id}"]
    # else:
    return base_namespace[unique_id]


# --- Flask Routes ---

@app.route('/')
def index():
    """Renders the main dynamic data entry form."""
    graph = get_graph()
    if graph is None:
        flash("Error: Knowledge Graph database is not available. Check logs.", "error")
        return render_template('error_page.html', message="Database unavailable") # A simple error page

    classes = get_ontology_classes(graph)
    if not classes:
        # Try reloading graph if empty? Or just show error.
        flash("Warning: No classes found in the ontology. Cannot add typed data.", "warning")
        # Optionally, attempt re-parsing here, but might indicate a deeper issue
        # get_graph(force_reload=True) # Need to modify get_graph for this
        # classes = get_ontology_classes(graph)

    return render_template('dynamic_form.html', classes=classes)

# --- API Endpoints for Dynamic Form ---

@app.route('/api/properties')
def api_get_properties():
    """Returns JSON list of properties for a given class URI."""
    graph = get_graph()
    if graph is None:
        return jsonify({"error": "Knowledge Graph not available"}), 500

    class_uri = request.args.get('class_uri')
    if not class_uri:
        return jsonify({"error": "Missing 'class_uri' parameter"}), 400

    properties = get_properties_for_class(graph, class_uri)
    return jsonify(properties)

@app.route('/api/instances')
def api_get_instances():
    """Returns JSON list of existing instances, optionally filtered by class URI."""
    graph = get_graph()
    if graph is None:
        return jsonify({"error": "Knowledge Graph not available"}), 500

    class_uri = request.args.get('class_uri') # Optional filter
    instances = get_existing_instances(graph, class_uri)
    return jsonify(instances)


# --- Data Addition Route ---

@app.route('/add_triple', methods=['POST'])
def add_triple():
    """Handles submission from the dynamic form."""
    graph = get_graph()
    if graph is None:
        flash("Error: Knowledge Graph database is not available.", "error")
        return redirect(url_for('index'))

    # --- Get Data from Form ---
    subject_name = request.form.get('subjectName')
    subject_class_uri = request.form.get('subjectClass')
    property_uri = request.form.get('property')
    object_kind = request.form.get('objectKind') # 'literal' or 'instance'
    object_value_literal = request.form.get('objectValueLiteral')
    object_uri_instance = request.form.get('objectValueInstance')

    # --- Basic Validation ---
    if not all([subject_name, subject_class_uri, property_uri, object_kind]):
        flash("Error: Subject Name, Class, Property, and Object Kind are required.", "error")
        return redirect(url_for('index'))

    if object_kind == 'literal' and not object_value_literal:
        flash("Error: Literal value cannot be empty when selected.", "error")
        return redirect(url_for('index'))

    if object_kind == 'instance' and not object_uri_instance:
        flash("Error: An existing instance must be selected.", "error")
        return redirect(url_for('index'))

    try:
        # --- Prepare URIs and Literals ---
        # Create a new URI for the subject instance using UUID
        subject_uri = mint_instance_uri(BASE_URI, subject_name)
        subject_class = URIRef(subject_class_uri)
        prop = URIRef(property_uri)

        # Determine the object
        obj = None
        object_display_value = "" # For flashing message
        if object_kind == 'literal':
            # For now, assume string literal. Could add type detection later based on property range (e.g., XSD.integer)
            obj = Literal(object_value_literal)
            object_display_value = f'"{object_value_literal}"'
        elif object_kind == 'instance':
            obj = URIRef(object_uri_instance)
            # Fetch label for the selected instance for better feedback
            object_display_value = f"<{get_label(obj, graph)}>" # Use helper to get label
        else:
            flash("Error: Invalid object kind specified.", "error")
            return redirect(url_for('index'))


        # --- Add Triples ---
        print(f"Adding triples for: {subject_name} ({subject_class_uri})")
        print(f"  Subject URI: {subject_uri}")
        print(f"  Property URI: {prop}")
        print(f"  Object: {obj} (Kind: {object_kind})")


        # 1. Add type triple for the new subject instance
        graph.add((subject_uri, RDF.type, subject_class))
        print(f"  Added: ({subject_uri.n3()}, {RDF.type.n3()}, {subject_class.n3()})")

        # 2. Add label/name for the new subject instance (use rdfs:label or your specific name property like ex:hasName)
        # Using rdfs:label is generally good practice
        graph.add((subject_uri, RDFS.label, Literal(subject_name)))
        # If you have a specific 'hasName' property, you might add that too/instead:
        # graph.add((subject_uri, EX.hasName, Literal(subject_name)))
        print(f"  Added: ({subject_uri.n3()}, {RDFS.label.n3()}, {Literal(subject_name).n3()})")


        # 3. Add the main relationship triple
        graph.add((subject_uri, prop, obj))
        print(f"  Added: ({subject_uri.n3()}, {prop.n3()}, {obj.n3()})")


        # Commit changes (SQLAlchemy store often auto-commits, but explicit can be safer depending on config/context)
        # graph.commit() # Maybe not necessary, test without it first. Add if triples don't persist reliably.

        # --- Success Feedback ---
        subject_label = subject_name
        property_label = get_label(prop, graph)

        message = f"Successfully added: '{subject_label}' ({get_label(subject_class, graph)}) --[{property_label}]--> {object_display_value}."
        flash(message, "success")
        print(message)

        # Optional: Render a result page or redirect back to form
        # return render_template('result.html', message=message, added_data={...})
        return redirect(url_for('index')) # Redirect back to form is common

    except Exception as e:
        print(f"ERROR: Failed to add triple to graph: {e}")
        # graph.rollback() # Rollback if using explicit transactions
        flash(f"Error adding data: {e}", "error")
        return redirect(url_for('index'))

# --- Optional: View Graph Route (Keep for Debugging) ---
@app.route('/view')
def view_graph():
    graph = get_graph()
    if graph is None:
        return "Error: Knowledge Graph database is not available.", 500

    format = request.args.get('format', 'html') # Allow requesting different formats

    if format == 'turtle':
        return graph.serialize(format='turtle'), 200, {'Content-Type': 'text/turtle'}
    elif format == 'xml':
        return graph.serialize(format='xml'), 200, {'Content-Type': 'application/rdf+xml'}
    elif format == 'nt':
         return graph.serialize(format='nt'), 200, {'Content-Type': 'application/n-triples'}
    else: # Default to simple HTML
        triples = []
        # Sort for slightly better readability
        for s, p, o in sorted(graph):
            # Make URIs clickable if possible (simple version)
            s_str = f'<a href="{s}" target="_blank">{get_label(s, graph)}</a>' if isinstance(s, URIRef) else get_label(s, graph)
            p_str = f'<a href="{p}" target="_blank">{get_label(p, graph)}</a>' if isinstance(p, URIRef) else get_label(p, graph)
            o_str = f'<a href="{o}" target="_blank">{get_label(o, graph)}</a>' if isinstance(o, URIRef) else get_label(o, graph)
            triples.append((s_str, p_str, o_str))

        html = "<h1>Knowledge Graph Triples</h1>"
        html += f"<p>Total triples: {len(graph)}</p>"
        html += "<p>View as: <a href='?format=turtle'>Turtle</a> | <a href='?format=xml'>RDF/XML</a> | <a href='?format=nt'>N-Triples</a></p>"
        html += "<table border='1'><tr><th>Subject</th><th>Predicate</th><th>Object</th></tr>"
        for s, p, o in triples:
            html += f"<tr><td>{s}</td><td>{p}</td><td>{o}</td></tr>"
        html += "</table>"
        return html

# --- Application Context Management ---
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Closes the store connection at the end of the request/context."""
    global store, g
    if store is not None:
       try:
           print("Closing RDFLib store connection.")
           store.close()
       except Exception as e:
           print(f"Error closing RDFLib store: {e}")
    # Reset g to ensure it's re-initialized on next request if needed
    # g = None # Careful: This might cause issues if requests overlap without proper context handling.
               # The get_graph logic should handle re-creation if store was closed.

# --- Application Startup ---
if __name__ == '__main__':
    # Ensure graph is initialized once before first request in development server
    # This helps catch initialization errors early.
    with app.app_context():
        print("Initializing graph for development server...")
        init_graph = get_graph()
        if init_graph is None:
            print("FATAL: Graph initialization failed on startup. Exiting.")
            # In a real deployment, you might want more robust handling here.
            exit(1) # Exit if DB/ontology connection fails fundamentally
        else:
            print(f"Graph initialized successfully with {len(init_graph)} triples.")

    # Run the Flask development server
    app.run(debug=True, host='0.0.0.0', port=5000)
