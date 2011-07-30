import sys, json
from datetime import datetime
from dateutil import tz

from webstore.client import Database

from helmut.core import app, solr
from helmut.core import database, types_table
from helmut.text import normalize, tokenize

def datetime_add_tz(dt):
    """ Solr requires time zone information on all dates. """
    return datetime(dt.year, dt.month, dt.day, dt.hour,
                    dt.minute, dt.second, tzinfo=tz.tzutc())

def query_filter(field, value, boost=None, fuzzy=False):
    """ Solr field filter factory. """
    value = value.replace('"', '\\"')
    filter_ = '%s:"%s"' % (field, value)
    if fuzzy:
        filter_ = '%s:%s~' % (field, value)
    else:
        value = value.replace('"', '\\"')
        filter_ = '%s:"%s"' % (field, value)
    if boost is not None and not fuzzy:
        filter_ += '^%d' % boost
    return filter_

class Type(object):

    def __init__(self, name, db_user, db_name, entity_table, entity_key, 
                 alias_table, alias_text, alias_key):
        self.name = name
        self.entity_table = entity_table
        self.entity_key = entity_key
        self.alias_text = alias_text
        self.alias_key = alias_key
        self.conn = solr()
        self.database = Database(app.config['WEBSTORE_SERVER'],
                                 db_user, db_name)
        self.alias = self.database[alias_table]
        self.entity = self.database[entity_table]
    
    def index(self, step=500):
        rows = []
        for i, row in enumerate(self.entity.traverse(_step=step)):
            row = self.row_to_index(row)
            rows.append(row)
            if i % step == 0:
                self.conn.add_many(rows, _commit=True)
                rows = []
        if len(rows):
            self.conn.add_many(rows)
        self.finalize()
        sys.stdout.write(" ok.\n")

    def row_to_index(self, row):
        key = row.get(self.entity_key)
        q = {self.alias_key: key}
        aliases = self.alias.traverse(**q)
        aliases = map(lambda a: a.get(self.alias_text), aliases)
        row['alias'] = aliases
        row['title.n'] = normalize(row.get('title'))
        row['alias.n'] = map(normalize, aliases)
        row['__type__'] = self.name
        row['__key__'] = key
        row['__id__'] = self.name + ':' + key
        sys.stdout.write('.')
        sys.stdout.flush()
        return row

    def finalize(self):
        """ After loading, run a few optimization operations. """
        self.conn.optimize()
        self.conn.commit()

    def by_key(self, key):
        return self.entity.find_one(**{self.entity_key: key})

    def find_block(self, text, filters=(), **kw):
        filters = list(filters)
        filters.append(('__type__',self.name))
        return self.find(text, filters=filters, **kw)

    @classmethod
    def find(cls, text, filters=(), facet_type=False, **kw):
        fq = ['+' + query_filter(k, v) for k, v in filters]
        if len(text) and text != '*:*':
            ntext = normalize(text)
            _q = [
                 query_filter('title', text, boost=10),
                 query_filter('title.n', ntext, boost=7),
                 query_filter('alias', text, boost=8, fuzzy=True),
                 query_filter('alias.n', ntext, boost=5, fuzzy=True),
                 query_filter('text', text, boost=2),
                 query_filter('text', ntext)
                 ]
            for token in tokenize(text):
                _q.append(query_filter('title', token, fuzzy=True, boost=4))
                _q.append(query_filter('alias', token, fuzzy=True, boost=3))
            for token in tokenize(ntext):
                _q.append(query_filter('title.n', token, fuzzy=True, boost=2))
                _q.append(query_filter('alias.n', token, fuzzy=True, boost=1))
            text = ' OR '.join(_q)
        if facet_type:
            kw['facet'] = 'true'
            kw['facet.field'] = kw.get('facet.field', []) + ['__type__']
            kw['facet.limit'] = 50
        result = solr().raw_query(q=text, fq=fq, wt='json',
                sort='score desc, title desc', fl='*,score', **kw)
        result = json.loads(result)
        return result

    @classmethod
    def _row_to_type(cls, row):
        return cls(row['name'],
                   row['db_user'],
                   row['db_name'],
                   row['entity_table'],
                   row['entity_key'],
                   row['alias_table'],
                   row['alias_text'],
                   row['alias_key'])

    @classmethod
    def types(cls):
        _types = []
        for row in types_table.traverse():
            _types.append(cls._row_to_type(row))
        return _types

    @classmethod
    def by_name(cls, name):
        row = types_table.find_one(name=name)
        if row is not None:
            row = cls._row_to_type(row)
        return row
