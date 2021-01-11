from configparser import ConfigParser
from os.path import dirname, join

BRAIN_API = "api-v11"
postgres_language_configurations = {
    'da': 'danish',
    'nl': 'dutch',
    'en': 'english',
    'fi': 'finnish',
    'fr': 'french',
    'de': 'german',
    'hu': 'hungarian',
    'it': 'italian',
    'no': 'norwegian',
    'pt': 'portuguese',
    'ro': 'romanian',
    'ru': 'russian',
    'es': 'spanish',
    'sv': 'swedish',
    'tr': 'turkish',
    'simple': 'simple',
}

config = ConfigParser()
config.read(join(dirname(dirname(__file__)),'config.ini'))
mbconfig = config['memebrane']
text_index_langs = set(mbconfig.get('text_index_langs', 'en').split(','))
assert all((lang in postgres_language_configurations for lang in text_index_langs)), "invalid languages"
