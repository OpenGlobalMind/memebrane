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

```shell
curl -LO https://github.com/ng110/TiddlPy/raw/5ced81288cbbb8f068a7f7c01c3f8ea3ce1d6b85/TiddlPy/TiddlPy.py
```

Run application:

```shell
flask run
```

## Usage

Visit the application at http://localhost:5000/

Thoughts are written to `tiddlywiki.html`, as well as displayed in the browser.

By default, “siblings” and the full JSON thought object are not displayed.

Add one of these query strings to the URL to display siblings or the full JSON:

```
?show=json
?show=siblings
?show=json,siblings
```

Remove the `?show=` component from the URL to hide those sections again.

## Possible Future Enhancements

* allow user to enter `brain_id` and `home_thought_id`
* add CSS and better web page layout
* add caching of retrieved thoughts
* do show/hide client side, rather than server side
* add “bread crumb” trail of visited thoughts
* do better with attachments that aren’t type 3

