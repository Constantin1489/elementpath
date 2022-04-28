#
# Copyright (c), 2018-2021, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
import datetime
import importlib
from itertools import chain
from types import ModuleType
from typing import TYPE_CHECKING, cast, Dict, Any, List, Iterator, \
    Optional, Sequence, Union, Callable, MutableMapping, Set, Tuple

from .exceptions import ElementPathTypeError, ElementPathValueError
from .namespaces import XML_NAMESPACE
from .datatypes import AnyAtomicType, Timezone
from .protocols import ElementProtocol, LxmlElementProtocol, \
    XsdElementProtocol, XMLSchemaProtocol
from .etree import ElementType, DocumentType, is_etree_element, \
    is_lxml_etree_element, etree_iter_root
from .xpath_nodes import DocumentNode, ElementNode, CommentNode, \
    ProcessingInstructionNode, AttributeNode, NamespaceNode, TextNode, \
    is_element_node, is_document_node, is_lxml_document_node, XPathNode, \
    XPathNodeType, is_schema, is_schema_node

if TYPE_CHECKING:
    from .xpath_token import XPathToken, XPathAxis


ContextRootType = Union[ElementType, DocumentType]
ContextItemType = Union[XPathNodeType, AnyAtomicType]


class XPathContext:
    """
    The XPath dynamic context. The static context is provided by the parser.

    Usually the dynamic context instances are created providing only the root element.
    Variable values argument is needed if the XPath expression refers to in-scope variables.
    The other optional arguments are needed only if a specific position on the context is
    required, but have to be used with the knowledge of what is their meaning.

    :param root: the root of the XML document, can be a ElementTree instance or an Element.
    :param namespaces: a dictionary with mapping from namespace prefixes into URIs, \
    used when namespace information is not available within document and element nodes. \
    This can be useful when the dynamic context has additional namespaces and root \
    is an Element or an ElementTree instance of the standard library.
    :param item: the context item. A `None` value means that the context is positioned on \
    the document node.
    :param position: the current position of the node within the input sequence.
    :param size: the number of items in the input sequence.
    :param axis: the active axis. Used to choose when apply the default axis ('child' axis).
    :param variables: dictionary of context variables that maps a QName to a value.
    :param current_dt: current dateTime of the implementation, including explicit timezone.
    :param timezone: implicit timezone to be used when a date, time, or dateTime value does \
    not have a timezone.
    :param documents: available documents. This is a mapping of absolute URI \
    strings onto document nodes. Used by the function fn:doc.
    :param collections: available collections. This is a mapping of absolute URI \
    strings onto sequences of nodes. Used by the XPath 2.0+ function fn:collection.
    :param default_collection: this is the sequence of nodes used when fn:collection \
    is called with no arguments.
    :param text_resources: available text resources. This is a mapping of absolute URI strings \
    onto text resources. Used by XPath 3.0+ function fn:unparsed-text/fn:unparsed-text-lines.
    :param resource_collections: available URI collections. This is a mapping of absolute \
    URI strings to sequence of URIs. Used by the XPath 3.0+ function fn:uri-collection.
    :param default_resource_collection: this is the sequence of URIs used when \
    fn:uri-collection is called with no arguments.
    :param allow_environment: defines if the access to system environment is allowed, \
    for default is `False`. Used by the XPath 3.0+ functions fn:environment-variable \
    and fn:available-environment-variables.
    """
    _parent_map: Optional[MutableMapping[ElementType, ContextRootType]] = None
    _etree: Optional[ModuleType] = None
    root: ContextRootType
    item: Optional[ContextItemType]
    total_nodes: int = 0  # Number of nodes associated to the context

    def __init__(self,
                 root: ContextRootType,
                 namespaces: Optional[Dict[str, str]] = None,
                 item: Optional[ContextItemType] = None,
                 position: int = 1,
                 size: int = 1,
                 axis: Optional[str] = None,
                 variables: Optional[Dict[str, Any]] = None,
                 current_dt: Optional[datetime.datetime] = None,
                 timezone: Optional[Union[str, Timezone]] = None,
                 documents: Optional[Dict[str, DocumentType]] = None,
                 collections: Optional[Dict[str, ElementType]] = None,
                 default_collection: Optional[str] = None,
                 text_resources: Optional[Dict[str, str]] = None,
                 resource_collections: Optional[Dict[str, List[str]]] = None,
                 default_resource_collection: Optional[str] = None,
                 allow_environment: bool = False,
                 default_language: Optional[str] = None,
                 default_calendar: Optional[str] = None,
                 default_place: Optional[str] = None) -> None:

        self.namespaces = namespaces
        self.root = root

        if is_element_node(root):
            if is_schema(root):
                self.root = self._build_schema_nodes(root)
            else:
                self.root = self._build_nodes(root)
            self.item = self.root if item is None else item
        elif is_document_node(root):
            self.root = self._build_nodes(root)
            self.item = item
        else:
            msg = "invalid root {!r}, an Element or an ElementTree or a schema instance required"
            raise ElementPathTypeError(msg.format(root))

        if isinstance(item, XPathNode):
            for _item in self.root.iter():
                if item == _item:
                    self.item = _item
                    break
        elif is_element_node(item):
            for _item in self.root.iter():
                if item is _item.value:
                    self.item = _item
                    break

        self.position = position
        self.size = size
        self.axis = axis

        if variables is None:
            self.variables = {}
        else:
            self.variables = {k: v for k, v in variables.items()}

        if timezone is None or isinstance(timezone, Timezone):
            self.timezone = timezone
        else:
            self.timezone = Timezone.fromstring(timezone)
        self.current_dt = current_dt or datetime.datetime.now(tz=self.timezone)

        self.documents = documents
        self.collections = collections
        self.default_collection = default_collection
        self.text_resources = text_resources if text_resources is not None else {}
        self.resource_collections = resource_collections
        self.default_resource_collection = default_resource_collection
        self.allow_environment = allow_environment
        self.default_language = default_language
        self.default_calendar = default_calendar
        self.default_place = default_place

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(root={self.root.value})'

    def __copy__(self) -> 'XPathContext':
        obj: XPathContext = object.__new__(self.__class__)
        obj.__dict__.update(self.__dict__)
        obj.axis = None
        obj.variables = {k: v for k, v in self.variables.items()}
        return obj

    def copy(self, clear_axis: bool = True) -> 'XPathContext':
        # Unused, so it could be deprecated in the future.
        obj: XPathContext = object.__new__(self.__class__)
        obj.__dict__.update(self.__dict__)
        if clear_axis:
            obj.axis = None
        obj.variables = {k: v for k, v in self.variables.items()}
        return obj

    @property
    def parent_map(self) -> MutableMapping[ElementType, ContextRootType]:
        if self._parent_map is None:
            self._parent_map: Dict[ElementType, ContextRootType]
            self._parent_map = {child: elem for elem in self.root.value.iter() for child in elem}
            if isinstance(self.root, DocumentNode):
                self._parent_map[cast(DocumentType, self.root.value).getroot()] = self.root

            # Add parent mapping for trees bound to dynamic context variables
            for v in self.variables.values():
                if is_document_node(v):
                    doc = cast(DocumentType, v)
                    self._parent_map.update((c, e) for e in doc.iter() for c in e)
                    self._parent_map[doc.getroot()] = doc
                elif is_element_node(v):
                    if isinstance(v, ElementNode):
                        root = v.elem
                    else:
                        root = cast(ElementType, v)

                    self._parent_map.update((c, e) for e in root.iter() for c in e)

        return self._parent_map

    @property
    def etree(self) -> ModuleType:
        if self._etree is None:
            etree_module_name = self.root.value.__class__.__module__
            self._etree: ModuleType = importlib.import_module(etree_module_name)
        return self._etree

    def get_root(self, node: Any) -> Union[None, ElementType, DocumentType]:
        if any(node == x for x in self.iter()):
            return self.root

        if self.documents is not None:
            try:
                for uri, doc in self.documents.items():
                    doc_context = XPathContext(root=doc)
                    if any(node == x for x in doc_context.iter()):
                        return doc
            except AttributeError:
                pass

        return None

    def get_parent(self, elem: Union[ElementType, ElementNode]) \
            -> Union[None, ElementType, DocumentType]:
        """Returns the parent of the element or `None` if it has no parent."""
        if isinstance(elem, XPathNode):
            return elem.parent

        _elem = elem.elem if isinstance(elem, ElementNode) else elem

        try:
            return self.parent_map[_elem]
        except KeyError:
            try:
                # fallback for lxml elements
                parent = _elem.getparent()  # type: ignore[union-attr]
            except AttributeError:
                return None
            else:
                return cast(Optional[ElementType], parent)

    def get_path(self, item: Any) -> str:
        """Cached path resolver for elements and attributes. Returns absolute paths."""
        path = []
        if isinstance(item, AttributeNode):
            path.append(f'@{item.name}')
            item = item.parent

        if item is None:
            return '' if not path else path[0]

        while True:
            try:
                path.append(item.tag)
            except AttributeError:
                pass  # is a document node

            item = item.parent
            if item is None:
                return '/{}'.format('/'.join(reversed(path)))

    def is_principal_node_kind(self) -> bool:
        if self.axis == 'attribute':
            return isinstance(self.item, AttributeNode)
        elif self.axis == 'namespace':
            return isinstance(self.item, NamespaceNode)
        else:
            return is_element_node(self.item)

    def match_name(self, name: Optional[str] = None,
                   default_namespace: Optional[str] = None) -> bool:
        """
        Returns `True` if the context item is matching the name, `False` otherwise.

        :param name: a fully qualified name, a local name or a wildcard. The accepted \
        wildcard formats are '*', '*:*', '*:local-name' and '{namespace}*'.
        :param default_namespace: the namespace URI associated with unqualified names. \
        Used for matching element names (tag).
        """
        if self.axis == 'attribute':
            if not isinstance(self.item, AttributeNode):
                return False
            item_name = self.item.name
        elif is_element_node(self.item):
            item_name = cast(ElementProtocol, self.item).tag
        else:
            return False

        if name is None or name == '*' or name == '*:*':
            return True

        if not name:
            return not item_name
        elif name[0] == '*':
            try:
                _, _name = name.split(':')
            except (ValueError, IndexError):
                raise ElementPathValueError("unexpected format %r for argument 'name'" % name)
            else:
                if item_name.startswith('{'):
                    return item_name.split('}')[1] == _name
                else:
                    return item_name == _name

        elif name[-1] == '*':
            if name[0] != '{' or '}' not in name:
                raise ElementPathValueError("unexpected format %r for argument 'name'" % name)
            elif item_name.startswith('{'):
                return item_name.split('}')[0][1:] == name.split('}')[0][1:]
            else:
                return False
        elif name[0] == '{' or not default_namespace:
            return item_name == name
        elif self.axis == 'attribute':
            return item_name == name
        else:
            return item_name == '{%s}%s' % (default_namespace, name)

    def _build_nodes(self, root: Union[DocumentType, ElementType]) -> Union[DocumentNode, ElementNode]:
        if hasattr(root, 'getroot'):
            document = cast(DocumentType, root)
            root_node = parent = DocumentNode(self, document)

            _root = document.getroot()
            for e in etree_iter_root(_root):
                if not callable(e.tag):
                    child = ElementNode(self, e, parent)
                    parent.children.append(child)
                elif e.tag.__name__ == 'Comment':
                    parent.children.append(CommentNode(self, e, parent))
                else:
                    parent.children.append(ProcessingInstructionNode(self, e, parent))

            children: Iterator[Any] = iter(_root)
            parent = child
        else:
            children: Iterator[Any] = iter(root)
            root_node = parent = ElementNode(self, root)

        iterators: List[Any] = []
        ancestors: List[Any] = []

        while True:
            try:
                elem = next(children)
            except StopIteration:
                try:
                    children, parent = iterators.pop(), ancestors.pop()
                except IndexError:
                    return root_node
            else:
                if not callable(elem.tag):
                    child = ElementNode(self, elem, parent)
                elif elem.tag.__name__ == 'Comment':
                    child = CommentNode(self, elem, parent)
                else:
                    child = ProcessingInstructionNode(self, elem, parent)

                parent.children.append(child)
                if elem.tail is not None:
                    parent.children.append(TextNode(self, elem.tail, parent, tail=True))

                if len(elem):
                    ancestors.append(parent)
                    parent = child
                    iterators.append(children)
                    children = iter(elem)

    def _build_schema_nodes(self, root: XMLSchemaProtocol) -> ElementNode:
        children: Iterator[Any] = iter(root)
        root_node = parent = ElementNode(self, root)

        elements = {root}
        iterators: List[Any] = []
        ancestors: List[Any] = []

        while True:
            try:
                elem = next(children)
            except StopIteration:
                try:
                    children, parent = iterators.pop(), ancestors.pop()
                except IndexError:
                    return root_node
            else:
                if elem in elements:
                    continue

                elements.add(elem)
                child = ElementNode(self, elem, parent)
                parent.children.append(child)

                if elem.ref is None:
                    ancestors.append(parent)
                    parent = child
                    iterators.append(children)
                    children = iter(elem)
                elif elem.ref not in elements:
                    elements.add(elem.ref)
                    ancestors.append(parent)
                    parent = child
                    iterators.append(children)
                    children = iter(elem.ref)

    @staticmethod
    def _iter_nodes(root: Union[DocumentNode, ElementNode], with_root: bool = True) \
            -> Iterator[Union[DocumentType, ElementType, TextNode]]:
        for node in root.iter(with_self=with_root):
            if isinstance(node, (DocumentNode, ElementNode, TextNode)):
                yield node

    def iter(self, namespaces: Optional[Dict[str, str]] = None) \
            -> Iterator[Union[ElementType, DocumentType, TextNode, NamespaceNode, AttributeNode]]:
        """
        Iterates context nodes in document order, including namespace and attribute nodes.

        :param namespaces: a fallback mapping for generating namespaces nodes, \
        used when element nodes do not have a property for in-scope namespaces.
        """
        yield from self.root.iter()

    def iter_results(self, results: Set[Any], namespaces: Optional[Dict[str, str]] = None) \
            -> Iterator[Optional[ContextItemType]]:
        """
        Generate results in document order.

        :param results: a container with selection results.
        :param namespaces: a fallback mapping for generating namespaces nodes, \
        used when element nodes do not have a property for in-scope namespaces.
        """
        status = self.root, self.item
        roots: Any
        root: Union[DocumentType, ElementType]

        documents = [v for v in results if is_document_node(v)]
        documents.append(self.root)
        documents.extend(v for v in self.variables.values() if is_document_node(v))
        visited_docs = set()

        for doc in documents:
            if doc in visited_docs:
                continue
            visited_docs.add(doc)

            self.root = doc
            for self.item in self.root.iter():
                if self.item in results:
                    yield self.item
                    results.remove(self.item)

                elif is_etree_element(self.item):
                    # Match XSD decoded elements
                    for typed_element in filter(lambda x: isinstance(x, ElementNode), results):
                        if typed_element.elem is self.item:
                            yield typed_element

        self.root, self.item = status

    def inner_focus_select(self, token: Union['XPathToken', 'XPathAxis']) -> Iterator[Any]:
        """Apply the token's selector with an inner focus."""
        status = self.item, self.size, self.position, self.axis
        results = [x for x in token.select(self.copy(clear_axis=False))]
        self.axis = None

        if token.label == 'axis' and cast('XPathAxis', token).reverse_axis:
            self.size = self.position = len(results)
            for self.item in results:
                yield self.item
                self.position -= 1
        else:
            self.size = len(results)
            for self.position, self.item in enumerate(results, start=1):
                yield self.item

        self.item, self.size, self.position, self.axis = status

    def iter_product(self, selectors: Sequence[Callable[[Any], Any]],
                     varnames: Optional[Sequence[str]] = None) -> Iterator[Any]:
        """
        Iterator for cartesian products of selectors.

        :param selectors: a sequence of selector generator functions.
        :param varnames: a sequence of variables for storing the generated values.
        """
        iterators = [x(self) for x in selectors]
        dimension = len(iterators)
        prod = [None] * dimension
        max_index = dimension - 1

        k = 0
        while True:
            try:
                value = next(iterators[k])
            except StopIteration:
                if not k:
                    return
                iterators[k] = selectors[k](self)
                k -= 1
            else:
                if varnames is not None:
                    try:
                        self.variables[varnames[k]] = value
                    except (TypeError, IndexError):
                        pass

                prod[k] = value
                if k == max_index:
                    yield tuple(prod)
                else:
                    k += 1

    ##
    # Context item iterators for axis

    def iter_self(self) -> Iterator[Any]:
        """Iterator for 'self' axis and '.' shortcut."""
        status = self.axis
        self.axis = 'self'
        yield self.item
        self.axis = status

    def iter_attributes(self) -> Iterator[AttributeNode]:
        """Iterator for 'attribute' axis and '@' shortcut."""
        status: Any

        if isinstance(self.item, AttributeNode):
            status = self.axis
            self.axis = 'attribute'
            yield self.item
            self.axis = status
            return
        elif isinstance(self.item, ElementNode) and self.item.context is not None:
            status = self.item, self.axis
            self.axis = 'attribute'

            for self.item in self.item.attributes:
                yield self.item

            self.item, self.axis = status
            return

        # TODO: remove this part after transition
        if not is_element_node(self.item):
            return

        status = self.item, self.axis
        self.axis = 'attribute'

        if isinstance(self.item, ElementNode):
            self.item = self.item.elem

        elem = cast(ElementType, self.item)
        for self.item in (AttributeNode(self, x[0], x[1], parent=elem) for x in elem.attrib.items()):
            yield self.item

        self.item, self.axis = status

    def iter_children_or_self(self) -> Iterator[Any]:
        """Iterator for 'child' forward axis and '/' step."""
        if self.axis is not None:
            yield self.item
            return

        status = self.item, self.axis
        self.axis = 'child'

        if isinstance(self.item, ElementNode):
            if self.item.context is None:
                self.item = self.item.elem
            else:
                for self.item in self.item.children:
                    yield self.item
                self.item, self.axis = status
                return

        if self.item is None:
            if isinstance(self.root, DocumentNode):
                for self.item in self.root:
                    yield self.item
                self.item, self.axis = status
                return

            if is_document_node(self.root):
                document = cast(DocumentType, self.root)
                root = document.getroot()
            else:
                root = cast(ElementProtocol, self.root)

            for self.item in etree_iter_root(root):
                yield self.item

        elif is_etree_element(self.item):
            elem = cast(ElementType, self.item)
            if callable(elem.tag):
                return
            elif elem.text is not None:
                self.item = TextNode(self, elem.text, elem)
                yield self.item

            for child in elem:
                self.item = child
                yield child

                if child.tail is not None:
                    self.item = TextNode(self, child.tail, child, True)
                    yield self.item

        elif isinstance(self.root, DocumentNode):
            for self.item in self.root:
                yield self.item

        elif is_document_node(self.item):
            document = cast(DocumentType, self.item)
            for self.item in etree_iter_root(document.getroot()):
                yield self.item

        self.item, self.axis = status

    def iter_parent(self) -> Iterator[ElementType]:
        """Iterator for 'parent' reverse axis and '..' shortcut."""
        if isinstance(self.item, ElementNode):
            if self.item.context is None:
                parent = self.get_parent(self.item.elem)
            else:
                parent = self.item.parent

        elif isinstance(self.item, TextNode):
            parent = self.item.parent
            if parent is not None and (callable(parent.tag) or self.item.is_tail()):
                parent = self.get_parent(parent)
        elif isinstance(self.item, XPathNode):
            parent = self.item.parent
        elif hasattr(self.item, 'tag'):
            parent = self.get_parent(cast(ElementType, self.item))
        else:
            return  # not applicable

        if parent is not None:
            status = self.item, self.axis
            self.axis = 'parent'

            self.item = parent
            yield cast(ElementType, self.item)

            self.item, self.axis = status

    def iter_siblings(self, axis: Optional[str] = None) \
            -> Iterator[Union[ElementType, TextNode]]:
        """
        Iterator for 'following-sibling' forward axis and 'preceding-sibling' reverse axis.

        :param axis: the context axis, default is 'following-sibling'.
        """
        if isinstance(self.item, (ElementNode, CommentNode, ProcessingInstructionNode)) \
                and self.item.context is not None:
            parent = self.item.parent
            if parent is None:
                return

            item = self.item
            status = self.item, self.axis
            self.axis = axis or 'following-sibling'

            if axis == 'preceding-sibling':
                for child in parent.children:  # pragma: no cover
                    if child is item:
                        break
                    self.item = child
                    yield child
            else:
                follows = False
                for child in parent:
                    if follows:
                        self.item = child
                        yield child
                    elif child is item:
                        follows = True
            self.item, self.axis = status
            return

        if isinstance(self.item, ElementNode):
            item = self.item.elem
        elif not is_etree_element(self.item) or callable(getattr(self.item, 'tag')):
            return
        else:
            item = cast(ElementType, self.item)

        parent = self.get_parent(item)
        if parent is None:
            return

        status = self.item, self.axis
        self.axis = axis or 'following-sibling'

        if axis == 'preceding-sibling':
            for child in parent:  # pragma: no cover
                if child is item:
                    break
                self.item = child
                yield child
                if child.tail is not None:
                    self.item = child.tail
                    yield self.item
        else:
            follows = False
            for child in parent:
                if follows:
                    self.item = child
                    yield child
                    if child.tail is not None:
                        self.item = child.tail
                        yield self.item
                elif child is item:
                    follows = True

        self.item, self.axis = status

    def iter_descendants(self, axis: Optional[str] = None,
                         inner_focus: bool = False) -> Iterator[Any]:
        """
        Iterator for 'descendant' and 'descendant-or-self' forward axes and '//' shortcut.

        :param axis: the context axis, for default has no explicit axis.
        :param inner_focus: if `True` splits between outer focus and inner focus. \
        In this case set the context size at start and change both position and \
        item at each iteration. For default only context item is changed.
        """
        descendants: Union[Iterator[Union[XPathNodeType, None]], Tuple[XPathNode]]
        with_self = axis != 'descendant'

        if isinstance(self.item, (ElementNode, DocumentNode)):
            descendants = self.item.iter_descendants(with_self)
        elif self.item is None:
            if isinstance(self.root, DocumentNode):
                descendants = self.root.iter(with_self)
            elif with_self:
                # Yields None in order to emulate position on document
                # FIXME replacing the self.root with ElementTree(self.root)?
                descendants = chain((None,), self.root.iter_descendats())
            else:
                descendants = self.root.iter_descendats()
        elif is_element_node(self.item) or is_document_node(self.item):
            print(f"Unwrapped {self.item}")
            raise RuntimeError()
        elif with_self and isinstance(self.item, XPathNode):
            descendants = self.item,
        else:
            return

        if inner_focus:
            status = self.item, self.position, self.size, self.axis
            self.axis = axis
            results = [e for e in descendants]

            self.size = len(results)
            for self.position, self.item in enumerate(results, start=1):
                yield self.item

            self.item, self.position, self.size, self.axis = status
        else:
            status_ = self.item, self.axis
            self.axis = axis
            for self.item in descendants:
                yield self.item
            self.item, self.axis = status_

    def iter_ancestors(self, axis: Optional[str] = None) -> Iterator[XPathNodeType]:
        """
        Iterator for 'ancestor' and 'ancestor-or-self' reverse axes.

        :param axis: the context axis, default is 'ancestor'.
        """
        if isinstance(self.item, ElementNode):
            if self.item.context:
                parent = self.item.parent
            else:
                parent = self.get_parent(self.item.elem)
        elif isinstance(self.item, TextNode):
            parent = self.item.parent
            if parent is not None and (callable(parent.tag) or self.item.is_tail()):
                parent = self.get_parent(parent)
        elif isinstance(self.item, XPathNode):
            parent = self.item.parent
        elif isinstance(self.item, AnyAtomicType):
            return
        elif self.item is None:
            return  # document position without a document root
        elif hasattr(self.item, 'tag'):
            parent = self.get_parent(cast(ElementType, self.item))
        elif is_document_node(self.item):
            parent = None
        else:
            return  # is not an XPath node

        status = self.item, self.axis
        self.axis = axis or 'ancestor'

        ancestors: List[Union[ElementType, DocumentType, XPathNode]] = []
        if axis == 'ancestor-or-self':
            ancestors.append(self.item)

        while parent is not None:
            ancestors.append(parent)
            parent = self.get_parent(parent)  # type: ignore[arg-type]

        for self.item in reversed(ancestors):
            yield self.item

        self.item, self.axis = status

    def iter_preceding(self) -> Iterator[ElementType]:
        """Iterator for 'preceding' reverse axis."""
        item: Union[ElementType, XPathNode]
        parent: Union[None, ElementType, DocumentType]

        if isinstance(self.item, ElementNode):
            if self.item.context:
                parent = self.item.parent
                item = self.item
                if parent is None:
                    return
            else:
                item = self.item.elem
                parent = self.get_parent(item)
        elif isinstance(self.item, XPathNode):
            item = self.item
            parent = item.parent
            if parent is None:
                return
            if callable(parent.tag):
                parent = self.get_parent(parent)
        else:
            return

        status = self.item, self.axis
        self.axis = 'preceding'

        ancestors = set()
        while parent is not None:
            ancestors.add(parent)
            parent = self.get_parent(parent)  # type: ignore[arg-type]

        for elem in self._iter_nodes(self.root):  # pragma: no cover
            if elem is item:
                break
            elif elem not in ancestors:
                self.item = cast(ElementType, elem)
                yield self.item

        self.item, self.axis = status

    def iter_followings(self) -> Iterator[XPathNodeType]:
        """Iterator for 'following' forward axis."""
        if self.item is None or self.item is self.root:
            return
        elif isinstance(self.item, ElementNode):
            status = self.item, self.axis
            self.axis = 'following'
            item = self.item

            descendants = set(item.iter_descendants())
            for self.item in self.root.iter():
                if item.index < self.item.index and self.item not in descendants:
                    yield self.item

            self.item, self.axis = status


class XPathSchemaContext(XPathContext):
    """
    The XPath dynamic context base class for schema bounded parsers. Use this class
    as dynamic context for schema instances in order to perform a schema-based type
    checking during the static analysis phase. Don't use this as dynamic context on
    XML instances.
    """
    iter_children_or_self: Callable[..., Iterator[Union[XsdElementProtocol, XMLSchemaProtocol]]]
    root: XMLSchemaProtocol
