import os
from flask import Flask, request, render_template, redirect, url_for, flash
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, OWL # Import common namespaces
from rdflib.store import Store
from rdflib.plugin import PluginException
from sqlalchemy.exc import OperationalError # To catch DB connection errors

# --- Configuration ---
app = Flask(__name__)
# Use environment variable for sensitive info like Secret Key in production
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key_for_dev')

# Get Database URL from environment variable. Fallback to SQLite for easy local dev.
# IMPORTANT: Use a persistent PostgreSQL URL for deployment (e.g., from Render, Neon, Supabase)
# Example PostgreSQL URL format: postgresql://user:password@host:port/database
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///rdf_knowledge_graph.db')
ONTOLOGY_FILE = 'ontology.ttl' # Path to your ontology file

# --- RDFLib Setup ---
# Define your ontology's namespace
EX = Namespace("http://example.org/ontology#")
# Define a base URI for instance data (can be different from ontology namespace)
BASE_URI = Namespace("http://example.org/data/")

# Configure the RDFLib store
# Use SQLAlchemy as the store type, connecting to your DATABASE_URL
# The identifier is a name for the specific graph within the store
graph_identifier = URIRef("urn:example:knowledge_graph")
store = None
g = None

def get_graph():
    """Initializes and returns the RDFLib Graph object."""
    global store, g
    if g is None:
        print(f"Connecting to RDF store at: {DATABASE_URL}")
        try:
            # Get the SQLAlchemy plugin store
            store = Graph(store='SQLAlchemy', identifier=graph_identifier)
            store.open(DATABASE_URL, create=True)
            g = store # Use the opened store as the graph object

            # Bind namespaces for nicer output (optional but recommended)
            g.bind("ex", EX)
            g.bind("rdf", RDF)
            g.bind("rdfs", RDFS)
            g.bind("owl", OWL)

            # Check if ontology is loaded (simple check: see if a class exists)
            # This prevents reloading the ontology on every startup if already in DB
            if (EX.Person, RDF.type, OWL.Class) not in g:
                print(f"Ontology not found in store. Parsing {ONTOLOGY_FILE}...")
                try:
                    g.parse(ONTOLOGY_FILE, format="turtle")
                    print(f"Ontology {ONTOLOGY_FILE} parsed and loaded into the store.")
                except FileNotFoundError:
                    print(f"ERROR: Ontology file '{ONTOLOGY_FILE}' not found.")
                except Exception as e:
                    print(f"ERROR: Failed to parse ontology file: {e}")
            else:
                print("Ontology already loaded in the store.")

        except PluginException as e:
            print(f"ERROR: RDFLib store plugin issue (SQLAlchemy not found or config error?): {e}")
            # Handle case where SQLAlchemy plugin isn't installed/found
            g = None # Ensure g remains None if setup failed
        except OperationalError as e:
            print(f"ERROR: Database connection failed ({DATABASE_URL}). Is the DB running? Correct credentials? Details: {e}")
            g = None # Ensure g remains None if setup failed
        except Exception as e:
            print(f"ERROR: An unexpected error occurred during graph initialization: {e}")
            g = None # Ensure g remains None if setup failed

    return g

# --- Flask Routes ---
@app.route('/')
def index():
    """Renders the main data entry form."""
    return render_template('index.html')

@app.route('/add', methods=['POST'])
def add_data():
    """Handles form submission to add Person-worksFor->Company data."""
    graph = get_graph()
    if graph is None:
        flash("Error: Knowledge Graph database is not available. Please check logs.", "error")
        return redirect(url_for('index'))

    person_name = request.form.get('personName')
    company_name = request.form.get('companyName')

    # Basic Validation
    if not person_name or not company_name:
        flash("Error: Both Person Name and Company Name are required.", "error")
        return redirect(url_for('index'))

    try:
        # Create URIs for the new instances (using simple name-based URIs for example)
        # WARNING: In production, use UUIDs or a more robust URI minting strategy
        # to avoid issues with spaces, duplicates, or special characters in names.
        person_uri = BASE_URI[person_name.replace(" ", "_")]
        company_uri = BASE_URI[company_name.replace(" ", "_")]

        # Check if entities already exist with the *same name* property
        # Note: A more robust check might query for the URI directly if using stable URIs like UUIDs
        person_exists = (person_uri, EX.hasName, Literal(person_name)) in graph
        company_exists = (company_uri, EX.hasName, Literal(company_name)) in graph

        # --- Add Triples ---
        # Add Person instance and type
        graph.add((person_uri, RDF.type, EX.Person))
        # Add/Update Person name (using add replaces previous name if URI exists)
        graph.add((person_uri, EX.hasName, Literal(person_name)))

        # Add Company instance and type
        graph.add((company_uri, RDF.type, EX.Company))
        # Add/Update Company name
        graph.add((company_uri, EX.hasName, Literal(company_name)))

        # Add the relationship
        graph.add((person_uri, EX.worksFor, company_uri))

        # Note: graph.commit() is generally not needed for adds with SQLAlchemy store,
        # as changes are often handled within the transaction context managed by the store/Flask.
        # If you bundle multiple operations and need transactional integrity,
        # you might interact with the underlying store's transaction methods.

        message = f"Successfully added: '{person_name}' ({'existing' if person_exists else 'new'}) works for '{company_name}' ({'existing' if company_exists else 'new'})."
        flash(message, "success")
        print(message) # Log success

        # Pass data to result page (optional)
        added_data = {
            "person": {"uri": str(person_uri), "name": person_name},
            "company": {"uri": str(company_uri), "name": company_name},
            "relationship": "worksFor"
        }
        return render_template('result.html', message=message, added_data=added_data)

    except Exception as e:
        # In production, log the exception traceback
        print(f"ERROR: Failed to add data to graph: {e}")
        # Consider rolling back changes if using explicit transactions
        # graph.rollback()
        flash(f"Error adding data to the knowledge graph: {e}", "error")
        return redirect(url_for('index'))

# Optional: Add a route to view triples (for debugging)
@app.route('/view')
def view_graph():
    graph = get_graph()
    if graph is None:
        return "Error: Knowledge Graph database is not available.", 500

    triples = []
    for s, p, o in graph:
        triples.append((str(s), str(p), str(o)))

    # Simple HTML output for viewing
    html = "<h1>Knowledge Graph Triples</h1><ul>"
    for s, p, o in triples:
        html += f"<li>{s} --- {p} --- {o}</li>"
    html += "</ul>"
    return html

# --- Application Startup ---
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Closes the store connection at the end of the request."""
    # SQLAlchemy store might manage sessions automatically,
    # but explicitly closing can be good practice for some stores.
    # For SQLAlchemy store, closing the graph object handles this.
    global store
    if store is not None:
       try:
           store.close()
       except Exception as e:
           print(f"Error closing RDFLib store: {e}")

if __name__ == '__main__':
    # Ensure graph is initialized on first request in development server
    with app.app_context():
        get_graph()
    # Run the Flask development server
    # Use host='0.0.0.0' to make it accessible on your network
    app.run(debug=True, host='0.0.0.0', port=5000)
