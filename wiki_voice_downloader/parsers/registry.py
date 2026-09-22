from ..errors import ParseError
from ..profile_loader import load_profiles
from .biligame_blhx import BiligameBlhxParser
from .koumakan_azurlane import KoumakanAzurLaneParser

STRATEGY_CLASSES={'biligame_blhx':BiligameBlhxParser,'koumakan_quotes':KoumakanAzurLaneParser}

def all_parsers(profiles=None):
    return [STRATEGY_CLASSES[p.data['strategy']](p) for p in (profiles if profiles is not None else load_profiles())]

def get_parser(url, parser_id=None, profiles=None):
    for parser in all_parsers(profiles):
        if (parser_id and parser.parser_id==parser_id) or (not parser_id and parser.supports(url)):
            return parser
    raise ParseError('unsupported_site: no matching registered parser','unsupported_site')
