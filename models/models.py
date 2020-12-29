from datetime import datetime
import enum
from logging import info
from re import A

from isodate import parse_datetime
from sqlalchemy import (
    Binary,
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
)
from sqlalchemy.exc import InterfaceError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, deferred
from sqlalchemy.orm.attributes import flag_modified
import requests
from sqlalchemy.sql.operators import is_distinct_from
from langdetect import detect_langs
from bleach import Cleaner

if True:
    from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
else:
    UUID = String
    JSONB = Text


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


class BaseOps(object):
    pass


Base = declarative_base(cls=BaseOps)


class Brain(Base):
    __tablename__ = "brain"
    id = Column(UUID, primary_key=True)
    name = Column(Unicode)
    slug = Column(String, unique=True)
    base_id = Column(UUID, nullable=True)

    @property
    def safe_slug(self):
        return self.slug or self.id


class Node(Base):
    __tablename__ = "node"
    __table_args__ = (
        Index("node_name_vidx",
              func.to_tsvector('simple', 'node.name'),
              postgresql_using='gin'),
        Index("node_tags_idx", 'tags', postgresql_using='gin'),
    )
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    tags = Column(ARRAY(UUID))
    name = Column(Unicode, nullable=False)
    last_read = Column(DateTime)
    last_modified = Column(DateTime)
    read_as_focus = Column(Boolean, default=False)
    is_tag = Column(Boolean)
    is_type = Column(Boolean)
    private = Column(Boolean)
    brain = relationship(Brain, foreign_keys=[brain_id])
    # siblings = relationship("Node", secondary="Link")

    def get_html_notes_att(self, session):
        return session.query(Attachment).filter_by(
            node=self, att_type=AttachmentType.NotesV9
        ).first()

    def get_html_notes(self, session):
        att = self.get_html_notes_att(session)
        if att:
            return att.text_content

    def get_md_notes_att(self, session):
        return session.query(Attachment).filter(
            Attachment.node == self, Attachment.att_type == AttachmentType.InternalFile,
            Attachment.location == "Notes.md", Attachment.data['noteType'] == "4"
        ).first()

    def get_md_notes(self, session):
        att = self.get_md_notes_att(session)
        if att:
            return att.text_content

    def get_neighbour_data(
            self, session, parents=True, children=True, siblings=True,
            jumps=True, tags=True, of_tags=True, full=False, with_links=False):
        queries = []
        entities = [Node] if full else [Node.id, Node.name]
        if with_links:
            entities.append(Link)
        if parents or siblings:
            parent_query = session.query(
                literal('parent'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & (Link.relation != LinkRelation.Jump))
        if parents:
            queries.append(parent_query)
        if children:
            queries.append(session.query(
                literal('child'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & (Link.relation != LinkRelation.Jump)))
        if siblings:
            subquery = parent_query.with_entities(Node.id).subquery()
            queries.append(session.query(
                literal('sibling'), *entities).join(
                Link, Node.parent_links).filter(
                Link.parent_id.in_(subquery) &
                (Node.id != self.id) & (Link.relation != LinkRelation.Jump)))
        if jumps:
            queries.append(session.query(
                literal('jump'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & (Link.relation == LinkRelation.Jump)))
            queries.append(session.query(
                literal('jump'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & (Link.relation == LinkRelation.Jump)))
        if tags and self.tags:
            query = session.query(
                literal('tag'), *entities).filter(Node.id.in_(self.tags))
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if of_tags:
            query = session.query(
                literal('of_tag'), *entities).filter(Node.tags.contains([self.id]))
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if not queries:
            return []
        query = queries.pop()
        if queries:
            query = query.union_all(*queries)
        return query.all()

    @classmethod
    def create_from_json(cls, data, focus=False):
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
            is_type=bool(data['kind'] & NodeType.Type.value),
            is_tag=bool(data['kind'] & NodeType.Tag.value),
            private=data.get('ACType', 0),
            tags=tags
        )

    @ classmethod
    def create_or_update_from_json(cls, session, data, force=False):
        i = session.query(cls).filter_by(id=data["id"]).first()
        if i:
            i.update_from_json(data, force)
        else:
            i = cls.create_from_json(data)
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
        self.data.update(data)
        flag_modified(self, 'data')
        self.name = data['name']
        self.last_modified = data_time
        self.is_type = bool(data['kind'] & NodeType.Type.value)
        self.is_tag = bool(data['kind'] & NodeType.Tag.value)
        self.private = data.get('ACType', 0)


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
            meaning=LinkMeaning._value2member_map_[data['meaning']],
            is_directed=bool(normalize_minus_one(
                data['direction']) & LinkDirection.IsDirected.value),
            is_one_way=bool(normalize_minus_one(
                data['direction']) & LinkDirection.OneWay.value),
            is_reversed=bool(normalize_minus_one(
                data['direction']) & LinkDirection.DirectionBA.value),
            link_type=LinkType._value2member_map_[data['kind']],
            last_modified=parse_datetime(data['modificationDateTime']),
            parent_id=data['thoughtIdA'],
            child_id=data['thoughtIdB']
        )

    @ classmethod
    def create_or_update_from_json(cls, session, data, force=False):
        i = session.query(cls).filter_by(id=data["id"]).first()
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
        self.meaning = LinkMeaning._value2member_map_[data['meaning']]
        self.is_directed = bool(normalize_minus_one(
            data['direction']) & LinkDirection.IsDirected.value)
        self.is_one_way = bool(normalize_minus_one(
            data['direction']) & LinkDirection.OneWay.value)
        self.is_reversed = bool(normalize_minus_one(
            data['direction']) & LinkDirection.DirectionBA.value)
        self.link_type = LinkType._value2member_map_[data['kind']]


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
    content = deferred(Column(Binary))
    text_content = Column(Text)
    inferred_locale = Column(String(3))
    node = relationship(Node, backref="attachments")
    brain = relationship(Brain, foreign_keys=[brain_id])
    __table_args__ = (
        Index("attachment_text_idx",
              func.to_tsvector('simple', text_content),
              postgresql_using='gin'),
        Index("attachment_text_en_idx",
              func.to_tsvector('english', text_content),
              postgresql_using='gin',
              postgres_where=inferred_locale=='en')
    )

    @ classmethod
    def create_or_update_from_json(cls, session, data, content=None, force=False):
        i = session.query(cls).filter_by(id=data["id"]).first()
        if i:
            i.update_from_json(data, content, force)
        else:
            i = cls.create_from_json(data, content)
        return i

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
        att_type=AttachmentType._value2member_map_[data['type']]
        text_content = None
        inferred_locale = None
        if self.att_type == AttachmentType.NotesV9 or (
                self.att_type == AttachmentType.InternalFile and
                self.data.get("noteType", 0) == 4):
            self.set_text_content(content.decode('utf-8'))
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
            att.set_content(content)

    def brain_uri(self):
        return f"https://api.thebrain.com/api-v11/brains/{self.brain_id}/thoughts/{self.node_id}/md-images/{self.location}"

    def populate_content(self, force=False):
        if self.content and not force:
            return
        if self.data["type"] in (1, 3, 4):  # links and notes
            return
        contentr = requests.get(self.brain_uri())
        if contentr.ok:
            self.content = contentr.content

    @ property
    def name(self):
        return self.data.get('name', self.data['location'])
