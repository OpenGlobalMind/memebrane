from flask import Flask, redirect, render_template, request
import requests
import json
import re

app = Flask(__name__)
app.config['FLASK_DEBUG'] = True
app.config['STATIC_FOLDER'] = '/static'
app.config['TEMPLATES_FOLDER'] = '/templates'

# Configure these as appropriate. The default values are for Jerry Michalski's TheBrain.
home_brain = {
    'name': "Jerry's Brain",
    'brain': '3d80058c-14d8-5361-0b61-a061f89baf87',
    'thought': '32f9fc36-6963-9ee0-9b44-a89112919e29'
}

def get_thought(brain_id, thought_id):
    r = requests.get('https://api.thebrain.com/api-v11/brains/' + brain_id + '/thoughts/' + thought_id + '/graph')
    return r.json()

@app.route("/")
def home():
    return redirect('/brain/' + home_brain['brain'] + '/thought/' + home_brain['thought'], code=302)

@app.route("/url", methods=['POST'])
def url():
    u = request.form['url'];

    # TODO: support URLs of the form https://webbrain.com/brainpage/brain/BED8187E-FD1C-CE55-E236-871DD7E1DF32#-6975

    # resolve shortened URL
    if u.startswith('https://bra.in/'):
        r = requests.get(u)
        u = r.url

    # check for reasonable URL
    if u.startswith('https://app.thebrain.com/'):
        pattern = re.compile(r'https://app.thebrain.com/brains/(?P<brain_id>[^/]+)/thoughts/(?P<thought_id>[^/]+)')
        match = pattern.match(u)
        if match is not None:
            return redirect('/brain/' + match.group('brain_id') + '/thought/' + match.group('thought_id'), code=302)

    # no joy
    return render_template(
        'url-error.html',
        bad_url = u
    )

@app.route("/brain/<brain_id>/thought/<thought_id>")
def get_thought_route(brain_id=None, thought_id=None):
    t = get_thought(brain_id, thought_id)

    # get show args
    show = request.args.get('show')
    if show:
        show_query_string = '?show={}'.format(show)
    else:
        show = ''
        show_query_string = ''

    # create a lookup table of names by thought_id
    thoughts = t['thoughts']
    names = {}
    for thought in thoughts:
        names[thought['id']] = thought['name']

    # render page
    root = t['root']
    return render_template(
        'index.html',
        json = json.dumps(t, indent=2),
        show = show,
        show_query_string = show_query_string,
        home_brain = home_brain,
        brain_id = brain_id,
        this_id = thought_id,
        names = names,
        parents = root['parents'],
        siblings = root['siblings'],
        jumps = root['jumps'],
        children = root['children'],
        attachments = t['attachments'],
        notes_html = t['notesHtml'],
    )
