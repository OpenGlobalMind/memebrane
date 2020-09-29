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

Visit the application at http://localhost:5000/

## Possible Future Enhancements

* parameterize `brain_id` and `home_thought_id`
* add CSS and better web page layout
* add caching of retrieved thoughts
* show/hide Siblings
* show/hide JSON representation of thoughts
* add “bread crumb” trail of visited thoughts
* display more parts of thoughts - description, attachments, URLs, etc.

