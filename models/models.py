from datetime import datetime
import enum
from logging import info
from itertools import groupby

from isodate import parse_datetime
from sqlalchemy import (
    BINARY,
    Boolean,
    Column,
    ForeignKey,
    String,
    Unicode,
    DateTime,
    Index,
    Text,
    literal,
    Enum,
    column,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import InterfaceError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func, cast, text
from sqlalchemy.orm import relationship, deferred, subqueryload, joinedload, aliased
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.future import select
from sqlalchemy.sql.operators import is_distinct_from
from sqlalchemy.sql.functions import count
from sqlalchemy.sql.type_api import TypeEngine
from langdetect import detect_langs
from bleach import Cleaner


if True:
    from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
    from sqlalchemy.dialects.postgresql.base import PGTypeCompiler

    class regconfig(TypeEngine):

        """Provide the PostgreSQL regconfig type.
        """

        __visit_name__ = "regconfig"


    PGTypeCompiler.visit_regconfig = lambda self, type_, **kw: "regconfig"

else:
    UUID = String
    JSONB = Text
from . import BRAIN_API, text_index_langs, postgres_language_configurations


def as_reg_class(lang='simple'):
    return cast(lang, regconfig)


cleaner = Cleaner(tags=[], strip=True, strip_comments=True)
class NodeType(enum.Enum):
    Normal = 1
    Type = 2
    Tag = 4


class LinkRelation(enum.Enum):
    NoValue = 0
    Child = 1
    Parent = 2
    Jump = 3
    Sibling = 4


class LinkDirection(enum.Enum):
    IsDirected = 1  # xxx1, 1 means Is-Directed
    DirectionBA = 2  # xx1x, 0 means A -> B, 1 means B\-> A, isBackward
    OneWay = 4  # x1xx, 1 means One-Way Link


class LinkMeaning(enum.Enum):
    Normal = 1
    InstanceOf = 2  # Type(A) to Normal Thought(B)
    TypeOf = 3  # Super Type(A) to Type(B)
    HasEvent = 4
    HasTag = 5  # Tag(A) to Normal or Type Thought(B)
    System = 6
    SubTagOf = 7  # Super Tag(A) to Tag(B)


class LinkType(enum.Enum):
    Normal = 1
    Type = 2


def normalize_minus_one(v, default=1):
    return default if v == -1 else v


class AttachmentType(enum.Enum):
    InternalFile = 1
    ExternalFile = 2
    ExternalUrl = 3
    NotesV9 = 4  # HTML-based notes from TheBrain 9 and 10
    Icon = 5
    NotesAsset = 6
    InternalDirectory = 7
    ExternalDirectory = 8
    SubFile = 9
    SubDirectory = 10
    SavedReport = 11
    MarkdownImage = 12


Base = declarative_base()


class Brain(Base):
    __tablename__ = "brain"
    id = Column(UUID, primary_key=True)
    name = Column(Unicode)
    slug = Column(String, unique=True)
    base_id = Column(UUID, nullable=True)

    @property
    def safe_slug(self):
        return self.slug or self.id

    async def top_node_id(self, session):
        "The ID of the public node with the most outgoing links."
        return await session.scalar(select(Link.parent_id).filter_by(brain_id=self.id
            ).join(Node, (Node.id==Link.parent_id) & (Node.private==False)
            ).group_by(Link.parent_id).order_by(count(Link.id).desc()).limit(1))

class Node(Base):
    __tablename__ = "node"
    __table_args__ = tuple([
        Index("node_tags_idx", 'tags', postgresql_using='gin'),
        Index("node_text_links_idx", 'text_links', postgresql_using='gin'),
        Index("node_name_vidx",
              func.to_tsvector(as_reg_class(), 'node.name'),
              postgresql_using='gin'),
    ] + [
        Index(f"node_name_{lang}_vidx",
              func.to_tsvector(as_reg_class(postgres_language_configurations[lang]), 'node.name'),
              postgresql_using='gin')
        for lang in text_index_langs
    ])
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    tags = Column(ARRAY(UUID))
    text_links = Column(ARRAY(UUID))
    name = Column(Unicode, nullable=False)
    last_read = Column(DateTime)
    last_modified = Column(DateTime)
    read_as_focus = Column(Boolean, default=False)
    is_tag = Column(Boolean)
    is_type = Column(Boolean)
    private = Column(Boolean)
    brain = relationship(Brain, foreign_keys=[brain_id])
    # siblings = relationship("Node", secondary="Link")

    def get_html_notes(self):
        atts = self.html_attachments
        if atts:
            return atts[0].text_content

    def get_notes_as_md(self):
        notes = self.get_md_notes()
        if notes:
            return notes
        notes = self.get_html_notes()
        if notes:
            from .utils import html_to_markdown
            return html_to_markdown(notes)

    def get_notes_as_html(self):
        notes = self.get_html_notes()
        if notes:
            return notes
        notes = self.get_md_notes()
        if notes:
            from .utils import process_markdown
            return process_markdown(notes)

    def get_md_notes(self):
        atts = self.md_attachments
        if atts:
            return atts[0].text_content

    def url_link(self):
        atts = self.url_link_attachments
        if atts:
            return atts[0].location

    async def gate_counts(self, session):
        sibling_link = aliased(Link)
        node_id = self.id
        neighbours = select(cast(node_id, UUID).label('id')).union_all(
        select(Link.parent_id.label('id')).where(Link.child_id == node_id),
        select(Link.child_id.label('id')).where(Link.parent_id == node_id),
        select(Link.child_id.label('id')).join(sibling_link, sibling_link.parent_id == Link.parent_id).where(sibling_link.child_id == node_id)).cte()

        r = await session.execute(select(
        literal('child').label('reln_type'),
        Node.id,
        count(Link.child_id)
        ).join(Link, Link.parent_id == Node.id
        ).filter(Node.id.in_(select(neighbours.c.id)), Link.relation != LinkRelation.Jump
        ).group_by(Node.id).union_all(
        select(
            literal('parent').label('reln_type'),
            Node.id,
            count(Link.parent_id)
            ).join(Link, Link.child_id == Node.id
            ).filter(Node.id.in_(select(neighbours.c.id)), Link.relation != LinkRelation.Jump
            ).group_by(Node.id),
        select(
            literal('jump').label('reln_type'),
            Node.id,
            count(Link.child_id)
            ).join(Link, Link.parent_id == Node.id
            ).filter(Node.id.in_(select(neighbours.c.id)), Link.relation == LinkRelation.Jump
            ).group_by(Node.id)).order_by(Node.id, 'reln_type'))

        counts = {}

        for node_id, data in groupby(r, lambda row: row[1]):
            data = dict([(name, count) for (name, _, count) in data])
            counts[node_id] = [data.get(name, 0) for name in ('child', 'parent', 'jump')]
        return counts

    async def get_neighbour_data(
            self, session, private=False, parents=True, children=True, siblings=True,
            jumps=True, tags=True, of_tags=True, full=False, text_links=False,
            text_backlinks=False, with_links=False, with_attachments=False):
        queries = []
        #import pdb; pdb.set_trace()
        entities = [Node] if full else [Node.id, Node.name]
        if with_links:
            entities.append(Link)
        if parents or siblings:
            parent_query = select(
                literal('parent').label('reln_type'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & (Link.relation != LinkRelation.Jump))
            if not private:
                parent_query = parent_query.filter(Node.private == False)
        if parents:
            queries.append(parent_query)
        if children:
            query = select(
                literal('child').label('reln_type'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & (Link.relation != LinkRelation.Jump))
            if not private:
                query = query.filter(Node.private == False)
            queries.append(query)
        if siblings:
            subquery = parent_query.with_only_columns(Node.id).subquery()
            query = select(
                literal('sibling').label('reln_type'), *entities).join(
                Link, Node.parent_links).filter(
                Link.parent_id.in_(select(subquery)) &
                (Node.id != self.id) & (Link.relation != LinkRelation.Jump))
            if not private:
                query = query.filter(Node.private == False)
            queries.append(query)
        if jumps:
            query = select(
                literal('jump').label('reln_type'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & (Link.relation == LinkRelation.Jump))
            if not private:
                query = query.filter(Node.private == False)
            queries.append(query)
            query = select(
                literal('jump').label('reln_type'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & (Link.relation == LinkRelation.Jump))
            if not private:
                query = query.filter(Node.private == False)
            queries.append(query)
        if tags and self.tags:
            query = select(
                literal('tag').label('reln_type'), *entities).filter(Node.id.in_(self.tags))
            if not private:
                query = query.filter(Node.private == False)
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if of_tags:
            query = select(
                literal('of_tag').label('reln_type'), *entities).filter(Node.tags.contains([self.id]))
            if not private:
                query = query.filter(Node.private == False)
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if text_links and self.text_links:
            query = select(
                literal('text_link').label('reln_type'), *entities).filter(
                    Node.id.in_(self.text_links), Node.brain_id==self.brain.id)
            if not private:
                query = query.filter(Node.private == False)
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if text_backlinks:
            query = select(
                literal('text_backlink').label('reln_type'), *entities).filter(
                    Node.text_links.contains([self.id]), Node.brain_id==self.brain.id)
            if not private:
                query = query.filter(Node.private == False)
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if not queries:
            return []
        query = queries.pop()
        if queries:
            query = query.union_all(*queries)
        query = query.order_by(column("reln_type"), Node.name)
        if full or with_links:
            query = select(column("reln_type"), *entities).from_statement(query)
        if with_attachments:
            query = query.options(
                joinedload(Node.html_attachments),
                joinedload(Node.md_attachments),
                subqueryload(Node.url_link_attachments))
        return await session.execute(query)

    @classmethod
    def create_from_json(cls, data, focus=False):
        from .utils import extract_text_links_from_data
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data.get('linksModificationDateTime', None)])))
        tags = data.pop('tags', None)
        if tags:
            tags = [t['id'] for t in tags]
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            name=data['name'],
            last_read=datetime.now(),
            last_modified=data_time,
            read_as_focus=focus,
            text_links=extract_text_links_from_data(data) if focus else [],
            is_type=bool(data['kind'] & NodeType.Type.value),
            is_tag=bool(data['kind'] & NodeType.Tag.value),
            private=data.get('ACType', 0),
            tags=tags
        )

    @ property
    def type_name(self):
        if self.is_type:
            return NodeType.Type.name
        elif self.is_tag:
            return NodeType.Tag.name
        else:
            return NodeType.Normal.name

    @ classmethod
    async def create_or_update_from_json(cls, session, data, focus=False, force=False):
        i = await session.scalar(select(cls).filter_by(id=data["id"]).limit(1))
        if i:
            i.update_from_json(data, focus, force)
        else:
            i = cls.create_from_json(data, focus)
        return i

    def update_from_json(self, data, focus=False, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data.get('linksModificationDateTime', None)])))
        if focus and not self.read_as_focus:
            force = True
            self.read_as_focus = True
        self.last_read = datetime.now()
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        tags = data.pop('tags', None)
        if tags:
            tags = [t['id'] for t in tags]
            self.tags = tags
        if focus:
            from .utils import extract_text_links_from_data
            self.text_links = extract_text_links_from_data(data)
        self.data.update(data)
        flag_modified(self, 'data')
        self.name = data['name']
        self.last_modified = data_time
        self.is_type = bool(data['kind'] & NodeType.Type.value)
        self.is_tag = bool(data['kind'] & NodeType.Tag.value)
        self.private = data.get('ACType', 0)

    @classmethod
    async def search(cls, session, brain, terms, start=0, limit=10, focus=False, lang=None, use_notes=False):
        pglang = 'simple'
        if lang in text_index_langs:
            pglang = postgres_language_configurations.get(lang, 'simple')
        txtarg1 = func.to_tsvector(as_reg_class(pglang), cls.name)
        filter = txtarg1.op('@@')(func.websearch_to_tsquery(as_reg_class(pglang), terms))
        rank = func.ts_rank(txtarg1, func.websearch_to_tsquery(as_reg_class(pglang), terms), 1)
        query = select(cls.id, cls.name).filter_by(brain=brain, private=False)
        if use_notes:
            query = query.outerjoin(Attachment)
            txtarg2 = func.to_tsvector(as_reg_class(pglang), Attachment.text_content)
            tfilter = txtarg2.op('@@')(func.websearch_to_tsquery(as_reg_class(pglang), terms))
            rank = func.coalesce(rank, 0) + 2 * func.coalesce(func.ts_rank_cd(txtarg2, func.websearch_to_tsquery(as_reg_class(pglang), terms), 1), 0)
            if lang:
                filter = filter | ((Attachment.inferred_locale == lang) & tfilter)
            else:
                filter = filter | tfilter
        return await session.execute(query.filter(filter).order_by(rank.desc()).offset(start).limit(limit))


class Link(Base):
    __tablename__ = "link"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    last_modified = Column(DateTime)
    relation = Column(Enum(LinkRelation))
    meaning = Column(Enum(LinkMeaning))
    link_type = Column(Enum(LinkType))
    is_directed = Column(Boolean, server_default='true')
    is_one_way = Column(Boolean, server_default='false')
    is_reversed = Column(Boolean, server_default='false')
    parent_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False, index=True)
    child_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False, index=True)
    parent = relationship(Node, foreign_keys=[
                          parent_id], backref="child_links")
    child = relationship(Node, foreign_keys=[child_id], backref="parent_links")

    @classmethod
    def create_from_json(cls, data):
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            relation=LinkRelation._value2member_map_[data['relation']],
            meaning=LinkMeaning._value2member_map_[data['meaning']
                ] if data['meaning'] else None,
            is_directed=bool(normalize_minus_one(
                data['direction']) & LinkDirection.IsDirected.value),
            is_one_way=bool(normalize_minus_one(
                data['direction']) & LinkDirection.OneWay.value),
            is_reversed=bool(normalize_minus_one(
                data['direction']) & LinkDirection.DirectionBA.value),
            link_type=LinkType._value2member_map_[data['kind']
                ] if data['kind'] else None,
            last_modified=parse_datetime(data['modificationDateTime']),
            parent_id=data['thoughtIdA'],
            child_id=data['thoughtIdB']
        )

    @ classmethod
    async def create_or_update_from_json(cls, session, data, force=False):
        i = await session.scalar(select(cls).filter_by(id=data["id"]).limit(1))
        if i:
            i.update_from_json(data, force)
        else:
            i = cls.create_from_json(data)
        return i

    def update_from_json(self, data, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(data['modificationDateTime'])
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        self.data = data
        self.last_modified = data_time
        self.parent_id = data['thoughtIdA']
        self.child_id = data['thoughtIdB']
        self.relation = LinkRelation._value2member_map_[data['relation']]
        self.meaning = LinkMeaning._value2member_map_[data['meaning']
            ] if data['meaning'] else None
        self.is_directed = bool(normalize_minus_one(
            data['direction']) & LinkDirection.IsDirected.value)
        self.is_one_way = bool(normalize_minus_one(
            data['direction']) & LinkDirection.OneWay.value)
        self.is_reversed = bool(normalize_minus_one(
            data['direction']) & LinkDirection.DirectionBA.value)
        self.link_type = LinkType._value2member_map_[data['kind']
            ] if data['kind'] else None


Node.children = relationship(
    Node, secondary=Link.__table__,
    primaryjoin=Node.id == Link.parent_id,
    secondaryjoin=Node.id == Link.child_id)
Node.parents = relationship(
    Node, secondary=Link.__table__,
    primaryjoin=Node.id == Link.child_id,
    secondaryjoin=Node.id == Link.parent_id)


class Attachment(Base):
    __tablename__ = "attachment"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    last_modified = Column(DateTime)
    location = Column(Unicode, nullable=False)
    node_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False)
    att_type = Column(Enum(AttachmentType))
    content = deferred(Column(BINARY))
    text_content = Column(Text)
    inferred_locale = Column(String(3))
    node = relationship(Node, backref="attachments")
    brain = relationship(Brain, foreign_keys=[brain_id])
    __table_args__ = tuple([
        Index("attachment_text_idx",
              func.to_tsvector(as_reg_class(), text_content),
              postgresql_using='gin')
    ] + [
        Index(f"attachment_text_{lang}_idx",
              func.to_tsvector(as_reg_class(postgres_language_configurations[lang]), 'text_content'),
              postgresql_using='gin',
              postgresql_where=f"inferred_locale=='{lang}'")
        for lang in text_index_langs
    ])

    @ classmethod
    async def create_or_update_from_json(cls, session, data, content=None, force=False):
        i = await session.scalar(select(cls).filter_by(id=data["id"]).limit(1))
        if i:
            i.update_from_json(data, content, force)
        else:
            i = cls.create_from_json(data, content)
        return i

    @ property
    def location_adjusted(self):
        if "/" not in self.location:
            return ".data/md-images/" + self.location
        return self.location

    def set_text_content(self, text_content):
        if text_content[0] == '\ufeff':
            text_content = text_content[1:]
        self.text_content = text_content
        if self.att_type == AttachmentType.NotesV9:
            text_content = cleaner.clean(text_content)
        try:
            langs = detect_langs(text_content)
            self.inferred_locale = langs[0].lang if langs else "zxx"
        except Exception:
            self.inferred_locale = "zxx"
        self.content = None

    def set_content(self, content):
        text_content = None
        inferred_locale = None
        if self.att_type == AttachmentType.NotesV9 or (
                self.att_type == AttachmentType.InternalFile and
                self.data.get("noteType", 0) == 4):
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            self.set_text_content(content)
        else:
            self.content = content
            self.text_content = None
            self.inferred_locale = None

    @ classmethod
    def create_from_json(cls, data, content=None):
        att = cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            last_modified=parse_datetime(data['modificationDateTime']),
            location=data['location'],
            att_type=AttachmentType._value2member_map_[data['type']],
            node_id=data['sourceId']
        )
        if content:
            att.set_content(content)
        return att

    def update_from_json(self, data, content=None, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(data['modificationDateTime'])
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        self.data = data
        self.last_modified = data_time
        self.location = data['location']
        self.node_id = data['sourceId']
        self.att_type = AttachmentType._value2member_map_[data['type']]
        if content:
            self.set_content(content)

    def brain_uri(self):
        return f"https://api.thebrain.com/{BRAIN_API}/brains/{self.brain_id}/thoughts/{self.node_id}/md-images/{self.location}"

    async def populate_content(self, httpx, force=False):
        if self.att_type in (
                AttachmentType.ExternalFile, AttachmentType.ExternalUrl,
                AttachmentType.ExternalDirectory):
            return
        if (self.text_content or self.content) and not force:
            return
        contentr = await httpx.get(self.brain_uri(), follow_redirects=True)
        if contentr.is_success:
            self.set_content(contentr.content)

    @ property
    def name(self):
        return self.data.get('name', self.data['location'])


Node.html_attachments = relationship(
    Attachment, primaryjoin=(Attachment.node_id==Node.id)
    & (Attachment.att_type==AttachmentType.NotesV9)
    & (Attachment.text_content != None))

Node.md_attachments = relationship(
    Attachment, primaryjoin=(Attachment.node_id==Node.id)
    & (Attachment.att_type==AttachmentType.InternalFile)
    & (Attachment.location == "Notes.md")
    & (Attachment.data['noteType'] == func.to_jsonb(4))
    & (Attachment.text_content != None))

Node.url_link_attachments = relationship(
    Attachment, primaryjoin=(Attachment.node_id==Node.id)
    & (Attachment.att_type==AttachmentType.ExternalUrl))
