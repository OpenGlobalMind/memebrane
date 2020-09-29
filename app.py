from flask import Flask, redirect, render_template
import requests

app = Flask(__name__)
app.config['FLASK_DEBUG'] = True
app.config['STATIC_FOLDER'] = '/static'
app.config['TEMPLATES_FOLDER'] = '/templates'

# Configure these as appropriate. The default values are for Jerry Michalski's TheBrain.
brain_id = '3d80058c-14d8-5361-0b61-a061f89baf87'
home_thought_id = '32f9fc36-6963-9ee0-9b44-a89112919e29'

def get_thought(thought_id):
    r = requests.get('https://api.thebrain.com/api-v11/brains/' + brain_id + '/thoughts/' + thought_id + '/graph')
    return r.json()

@app.route("/")
def home():
    return redirect('/thought/' + home_thought_id, code=302)

@app.route("/thought/<thought_id>")
def get_thought_route(thought_id=None):
    t = get_thought(thought_id)

    # create a lookup table of names by thought_id
    thoughts = t['thoughts']
    names = {}
    for thought in thoughts:
        names[thought['id']] = thought['name']

    # render page
    root = t['root']
    return render_template(
        'index.html',
        this_id = thought_id,
        names = names,
        parents = root['parents'],
        siblings = root['siblings'],
        jumps = root['jumps'],
        children = root['children'],
        attachments = root['attachments'],
    )
