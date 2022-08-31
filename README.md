# MemeBrane

MemeBrane is a web-based Flask application that browses a public TheBrain graph database.

**meme** (n.) - a small particle of human culture.

**brane** (n.) - an object that generalizes the notion of a point particle to higher dimensions.

## Installation

Clone this repo and cd into its directory.

Set up a Python virtual environment with `venv` or`virtualenv`:

```shell
virtualenv -p python3 venv
source venv/bin/activate
```

Install libraries:

```shell
pip install -r requirements.txt
```

Run application:

```shell
flask run
```

## Usage

Visit the application at http://localhost:5000/
It will lead you to the default thought for the default brain. (We need to use the brain list instead.)

It is also possible to ask for the data in 'text/csv' or 'application/json' with Accept: mimetype header.
The following list shows what information is included in the views. The defaults are given for the html view; the data views include more links by default.

* `json` (False): Show the raw json in the html view.
* `parents` (True): Show the parents of the node
* `children` (True): Show the children of the node
* `siblings` (True): Show siblings of the node (unless the parent is a type)
* `jumps` (True): Show jump links starting from the node
* `tags` (True): Show tag nodes associated with the node
* `of_tags` (True): Show nodes with that tag (on a tag node)
* `same_type` (False): Show other nodes sharing the types of this node (siblings through type)
* `text_links` (False): Show nodes linked to this node through a link in the body text
* `text_backlinks` (False): Show nodes that link to this node through a link in the body text
* `with_attachments` (False): Include attachment information for all related nodes
* `gate_counts` (False): Include `gate_counts` in json data

The defaults given can be changed either directly with GET arguments (`?arg1=true&arg2=false`), or in the form of a single argument `?show=arg1,-arg2,...`

## Possible Future Enhancements

* allow user to enter `brain_id` and `home_thought_id`
* add CSS and better web page layout
* add caching of retrieved thoughts
* do show/hide client side, rather than server side
* add “bread crumb” trail of visited thoughts
* do better with attachments that aren’t type 3

