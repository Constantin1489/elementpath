#
# Copyright (c), 2018-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
XPathToken and helper functions for XPath nodes. XPath error messages and node helper functions
are embedded in XPathToken class, in order to raise errors related to token instances.

In XPath there are 7 kinds of nodes:

    element, attribute, text, namespace, processing-instruction, comment, document

Element-like objects are used for representing elements and comments, ElementTree-like objects
for documents. Generic tuples are used for representing attributes and named-tuples for namespaces.
"""
import locale
import contextlib
import math
from copy import copy
from decimal import Decimal
from itertools import product
import urllib.parse

from .exceptions import ElementPathError, ElementPathValueError, XPATH_ERROR_CODES
from .namespaces import XQT_ERRORS_NAMESPACE, XSD_NAMESPACE, XPATH_FUNCTIONS_NAMESPACE, \
    XSD_ANY_TYPE, XSD_ANY_SIMPLE_TYPE, XSD_ANY_ATOMIC_TYPE
from .xpath_nodes import XPathNode, AttributeNode, TextNode, NamespaceNode, TypedAttribute, \
    TypedElement, is_etree_element, etree_iter_strings, is_comment_node, \
    is_processing_instruction_node, is_element_node, is_document_node, \
    is_xpath_node, is_schema_node
from .datatypes import xsd10_atomic_types, xsd11_atomic_types, AbstractDateTime, \
    AnyURI, UntypedAtomic, Timezone, DateTime10, Date10, DayTimeDuration, Duration, \
    Integer, DoubleProxy10, DoubleProxy, QName
from .schema_proxy import AbstractSchemaProxy
from .tdop import Token, MultiLabel
from .xpath_context import XPathSchemaContext

UNICODE_CODEPOINT_COLLATION = "http://www.w3.org/2005/xpath-functions/collation/codepoint"

XSD_SPECIAL_TYPES = {XSD_ANY_TYPE, XSD_ANY_SIMPLE_TYPE, XSD_ANY_ATOMIC_TYPE}


def ordinal(n):
    if n in {11, 12, 13}:
        return '%dth' % n

    least_significant_digit = n % 10
    if least_significant_digit == 1:
        return '%dst' % n
    elif least_significant_digit == 2:
        return '%dnd' % n
    elif least_significant_digit == 3:
        return '%drd' % n
    else:
        return '%dth' % n


class XPathToken(Token):
    """Base class for XPath tokens."""
    xsd_types = None  # for XPath 2.0+ schema types labeling
    namespace = None  # for namespace binding of names and wildcards

    def evaluate(self, context=None):
        """
        Evaluate default method for XPath tokens.

        :param context: The XPath dynamic context.
        """
        return [x for x in self.select(context)]

    def select(self, context=None):
        """
        Select operator that generates XPath results.

        :param context: The XPath dynamic context.
        """
        item = self.evaluate(context)
        if item is not None:
            if isinstance(item, list):
                yield from item
            else:
                if context is not None:
                    context.item = item
                yield item

    def __str__(self):
        symbol, label = self.symbol, self.label
        if symbol == '$':
            return '$%s variable reference' % (self[0].value if self._items else '')
        elif symbol == ',':
            return 'comma operator' if self.parser.version > '1.0' else 'comma symbol'
        elif label.endswith('function') or label in ('axis', 'sequence type', 'kind test'):
            return '%r %s' % (symbol, label)
        return super(XPathToken, self).__str__()

    @property
    def source(self):
        symbol, label = self.symbol, self.label
        if label == 'axis':
            return '%s::%s' % (self.symbol, self[0].source)
        elif label.endswith('function') or label in ('sequence type', 'kind test'):
            return '%s(%s)' % (self.symbol, ', '.join(item.source for item in self))
        elif symbol == ':':
            return '%s:%s' % (self[0].source, self[1].source)
        elif symbol == '(':
            return '()' if not self else '(%s)' % self[0].source
        elif symbol == '[':
            return '%s[%s]' % (self[0].source, self[1].source)
        elif symbol == ',':
            return '%s, %s' % (self[0].source, self[1].source)
        elif symbol == '$':
            return '$%s' % self[0].source
        elif symbol == '{':
            return '{%s}%s' % (self[0].value, self[1].value)
        elif symbol == 'instance':
            return '%s instance of %s' % (self[0].source, ''.join(t.source for t in self[1:]))
        elif symbol == 'treat':
            return '%s treat as %s' % (self[0].source, ''.join(t.source for t in self[1:]))
        elif symbol == 'for':
            return 'for %s return %s' % (
                ', '.join('%s in %s' % (self[k].source, self[k + 1].source)
                          for k in range(0, len(self) - 1, 2)),
                self[-1].source
            )
        return super(XPathToken, self).source

    @property
    def child_axis(self):
        """Is `True` if the token apply child axis for default, `False` otherwise."""
        if self.symbol not in {'*', 'node', 'child', 'text', '(name)', ':',
                               'document-node', 'element', 'schema-element'}:
            return False
        elif self.symbol != ':':
            return True
        return not self[1].label.endswith('function')

    ###
    # Tokens tree analysis methods
    def iter_leaf_elements(self):
        """
        Iterates through the leaf elements of the token tree if there are any,
        returning QNames in prefixed format. A leaf element is an element
        positioned at last path step. Does not consider kind tests and wildcards.
        """
        if self.symbol in {'(name)', ':'}:
            yield self.value
        elif self.symbol in ('//', '/'):
            if self._items[-1].symbol in {
                '(name)', '*', ':', '..', '.', '[', 'self', 'child',
                'parent', 'following-sibling', 'preceding-sibling',
                'ancestor', 'ancestor-or-self', 'descendant',
                'descendant-or-self', 'following', 'preceding'
            }:
                yield from self._items[-1].iter_leaf_elements()

        elif self.symbol in ('[',):
            yield from self._items[0].iter_leaf_elements()
        else:
            for tk in self._items:
                yield from tk.iter_leaf_elements()

    ###
    # Dynamic context methods
    def get_argument(self, context, index=0, required=False, default_to_context=False,
                     default=None, cls=None, promote=None):
        """
        Get the argument value of a function of constructor token. A zero length sequence is
        converted to a `None` value. If the function has no argument returns the context's
        item if the dynamic context is not `None`.

        :param context: the dynamic context.
        :param index: an index for select the argument to be got, the first for default.
        :param required: if set to `True` missing or empty sequence arguments are not allowed.
        :param default_to_context: if set to `True` then the item of the dynamic context is \
        returned when the argument is missing.
        :param default: the default value returned in case the argument is an empty sequence. \
        If not provided returns `None`.
        :param cls: if a type is provided performs a type checking on item.
        :param promote: a class or a tuple of classes that are promoted to `cls` class.
        """
        try:
            selector = self._items[index].select
        except IndexError:
            if default_to_context:
                if context is None:
                    raise self.missing_context() from None
                item = context.item if context.item is not None else context.root
            elif required:
                msg = "missing %s argument" % ordinal(index + 1)
                raise self.error('XPST0017', msg) from None
            else:
                return
        else:
            item = None
            for k, result in enumerate(selector(copy(context))):
                if k == 0:
                    item = result
                elif self.parser.compatibility_mode:
                    break
                elif isinstance(context, XPathSchemaContext):
                    # Multiple schema nodes are ignored but do not raise. The target
                    # of schema context selection is XSD type association and multiple
                    # nodes coherency is already checked at schema level.
                    break
                else:
                    raise self.wrong_context_type(
                        "a sequence of more than one item is not allowed as argument"
                    )
            else:
                if item is None:
                    if not required:
                        return default
                    ord_arg = ordinal(index + 1)
                    msg = "A not empty sequence required for {} argument"
                    raise self.error('XPTY0004', msg.format(ord_arg))

        # Type promotion checking (see "function conversion rules" in XPath 2.0 language definition)
        if cls is not None and not isinstance(item, cls):
            if promote and isinstance(item, promote):
                return cls(item)

            if self.parser.compatibility_mode:
                if issubclass(cls, str):
                    return self.string_value(item)
                elif issubclass(cls, float) or issubclass(float, cls):
                    return self.number_value(item)

            if self.parser.version == '1.0':
                code = 'XPTY0004'
            else:
                value = self.data_value(item)
                if isinstance(value, cls):
                    return value
                elif isinstance(value, UntypedAtomic):
                    try:
                        if issubclass(cls, str):
                            return str(value)
                        else:
                            return cls(value)
                    except (TypeError, ValueError):
                        pass

                code = 'FOTY0012' if value is None else 'XPTY0004'

            message = "the type of the {} argument is {!r} instead of {!r}"
            raise self.error(code, message.format(ordinal(index + 1), type(item), cls))

        return item

    def select_data_values(self, context=None):
        """
        Yields data value of selected items.

        :param context: the XPath dynamic context.
        """
        for item in self.select(context):
            yield self.data_value(item)

    def atomization(self, context=None):
        """
        Helper method for value atomization of a sequence.

        Ref: https://www.w3.org/TR/xpath20/#id-atomization

        :param context: the XPath dynamic context.
        """
        for item in self.select(context):
            value = self.data_value(item)
            if value is None:
                msg = "argument node {!r} does not have a typed value"
                raise self.error('FOTY0012', msg.format(item))
            else:
                yield value

    def get_atomized_operand(self, context=None):
        """
        Get the atomized value for an XPath operator.

        :param context: the XPath dynamic context.
        :return: the atomized value of a single length sequence or `None` if the sequence is empty.
        """
        selector = iter(self.atomization(context))
        try:
            value = next(selector)
        except StopIteration:
            return
        else:
            item = getattr(context, 'item', None)

            try:
                next(selector)
            except StopIteration:
                if isinstance(value, UntypedAtomic):
                    value = str(value)

                if not isinstance(context, XPathSchemaContext) and \
                        item is not None and \
                        self.xsd_types and \
                        isinstance(value, str):

                    xsd_type = self.get_xsd_type(item)
                    if xsd_type is None or xsd_type.name in XSD_SPECIAL_TYPES:
                        pass
                    else:
                        try:
                            value = xsd_type.decode(value)
                        except (TypeError, ValueError):
                            msg = "Type {!r} is not appropriate for the context"
                            raise self.wrong_context_type(msg.format(type(value)))

                return value
            else:
                msg = "atomized operand is a sequence of length greater than one"
                raise self.wrong_context_type(msg)

    def iter_comparison_data(self, context):
        """
        Generates comparison data couples for the general comparison of sequences.
        Different sequences maybe generated with an XPath 2.0 parser, depending on
        compatibility mode setting.

        Ref: https://www.w3.org/TR/xpath20/#id-general-comparisons

        :param context: the XPath dynamic context.
        """
        if self.parser.compatibility_mode:
            operand1 = [x for x in self._items[0].select(copy(context))]
            operand2 = [x for x in self._items[1].select(copy(context))]

            # Boolean comparison if one of the results is a single boolean value (1.)
            try:
                if isinstance(operand1[0], bool):
                    if len(operand1) == 1:
                        yield operand1[0], self.boolean_value(operand2)
                        return
                if isinstance(operand2[0], bool):
                    if len(operand2) == 1:
                        yield self.boolean_value(operand1), operand2[0]
                        return
            except IndexError:
                return

            # Converts to float for lesser-greater operators (3.)
            if self.symbol in ('<', '<=', '>', '>='):
                yield from product(map(float, map(self.data_value, operand1)),
                                   map(float, map(self.data_value, operand2)))
                return
            elif self.parser.version == '1.0':
                yield from product(map(self.data_value, operand1), map(self.data_value, operand2))
                return

        for values in product(map(self.data_value, self._items[0].select(copy(context))),
                              map(self.data_value, self._items[1].select(copy(context)))):
            if any(isinstance(x, bool) for x in values):
                if any(isinstance(x, (str, Integer)) for x in values):
                    msg = "cannot compare {!r} and {!r}"
                    raise TypeError(msg.format(type(values[0]), type(values[1])))
            elif any(isinstance(x, Integer) for x in values) and \
                    any(isinstance(x, str) for x in values):
                msg = "cannot compare {!r} and {!r}"
                raise TypeError(msg.format(type(values[0]), type(values[1])))
            yield values

    def select_results(self, context):
        """
        Generates formatted XPath results.

        :param context: the XPath dynamic context.
        """
        self.parser.check_variables(context.variables)

        for result in self.select(context):
            if isinstance(result, TypedElement):
                yield result[0]
            elif isinstance(result, (TextNode, AttributeNode)):
                yield result.value
            elif isinstance(result, TypedAttribute):
                if is_schema_node(result[0].value):
                    yield result[0].value
                else:
                    yield result.value
            elif isinstance(result, NamespaceNode):
                yield result.prefix, result.uri
            else:
                yield result

    def get_results(self, context):
        """
        Returns formatted XPath results.

        :param context: the XPath dynamic context.
        :return: a list or a simple datatype when the result is a single simple type \
        generated by a literal or function token.
        """
        self.parser.check_variables(context.variables)

        results = [x for x in self.select_results(context)]
        if len(results) == 1:
            res = results[0]
            if isinstance(res, (bool, int, float, Decimal)):
                return res
            elif isinstance(res, (tuple, XPathNode)) \
                    or is_etree_element(res) or is_document_node(res):
                return results
            elif is_schema_node(res):
                return results
            elif self.symbol in ('text', 'node'):
                return results
            elif self.label in ('function', 'literal'):
                return res
            else:
                return results
        else:
            return results

    def get_operands(self, context, cls=None):
        """
        Returns the operands for a binary operator. Float arguments are converted
        to decimal if the other argument is a `Decimal` instance.

        :param context: the XPath dynamic context.
        :param cls: if a type is provided performs a type checking on item.
        :return: a couple of values representing the operands. If any operand \
        is not available returns a `(None, None)` couple.
        """
        op1 = self.get_argument(context, cls=cls)
        if op1 is None:
            return None, None
        elif is_element_node(op1):
            op1 = self[0].data_value(op1)

        op2 = self.get_argument(context, index=1, cls=cls)
        if op2 is None:
            return None, None
        elif is_element_node(op2):
            op2 = self[1].data_value(op2)

        if isinstance(op1, AbstractDateTime) and isinstance(op2, AbstractDateTime):
            if context is not None and context.timezone is not None:
                if op1.tzinfo is None:
                    op1.tzinfo = context.timezone
                if op2.tzinfo is None:
                    op2.tzinfo = context.timezone
        else:
            if isinstance(op1, UntypedAtomic):
                op1 = self.cast_to_double(op1.value)
                if isinstance(op2, Decimal):
                    return op1, float(op2)
            if isinstance(op2, UntypedAtomic):
                op2 = self.cast_to_double(op2.value)
                if isinstance(op1, Decimal):
                    return float(op1), op2

        if isinstance(op1, float):
            if isinstance(op2, Duration):
                return Decimal(op1), op2
            if isinstance(op2, Decimal):
                return op1, type(op1)(op2)
        if isinstance(op2, float):
            if isinstance(op1, Duration):
                return op1, Decimal(op2)
            if isinstance(op1, Decimal):
                return type(op2)(op1), op2

        return op1, op2

    def get_absolute_uri(self, uri, base_uri=None):
        """
        Obtains an absolute URI from the argument and the static context.

        :param uri: a string representing an URI.
        :param base_uri: an alternative base URI, otherwise the base_uri \
        of the static context is used.
        :returns: the argument if it's an absolute URI. Otherwise returns the URI
        obtained by the join o the base_uri of the static context with the
        argument. Returns the argument if the base_uri is `None'.
        """
        if not base_uri:
            base_uri = self.parser.base_uri

        url_parts = urllib.parse.urlparse(uri)
        if url_parts.scheme or url_parts.netloc \
                or url_parts.path.startswith('/') \
                or base_uri is None:
            return uri

        url_parts = urllib.parse.urlsplit(base_uri)
        if url_parts.fragment or not url_parts.scheme and \
                not url_parts.netloc and not url_parts.path.startswith('/'):
            raise self.error('FORG0002', '{!r} is not suitable as base URI'.format(base_uri))

        return urllib.parse.urljoin(base_uri, uri)

    def get_namespace(self, prefix):
        """
        Resolves a prefix to a namespace raising an error (FONS0004) if the
        prefix is not found in the namespace map.
        """
        try:
            return self.parser.namespaces[prefix]
        except KeyError as err:
            msg = 'no namespace found for prefix %r' % str(err)
            raise self.error('FONS0004', msg) from None

    def bind_namespace(self, namespace):
        """
        Bind a token with a namespace. The token has to be a name, a name wildcard,
        a function or a constructor, otherwise a syntax error is raised. Functions
        and constructors must be limited to its namespaces.
        """
        if self.symbol in ('(name)', '*'):
            pass
        elif namespace == XPATH_FUNCTIONS_NAMESPACE:
            if self.label != 'function':
                msg = "a name, a wildcard or a function expected"
                raise self.wrong_syntax(msg, code='XPST0017')
            elif isinstance(self.label, MultiLabel):
                self.label = 'function'
        elif namespace == XSD_NAMESPACE:
            if self.label != 'constructor function':
                msg = "a name, a wildcard or a constructor function expected"
                raise self.wrong_syntax(msg, code='XPST0017')
            elif isinstance(self.label, MultiLabel):
                self.label = 'constructor function'
        else:
            raise self.wrong_syntax("a name, a wildcard or a function expected")

        self.namespace = namespace

    def adjust_datetime(self, context, cls):
        """
        XSD datetime adjust function helper.

        :param context: the XPath dynamic context.
        :param cls: the XSD datetime subclass to use.
        :return: an empty list if there is only one argument that is the empty sequence \
        or the adjusted XSD datetime instance.
        """
        if len(self) == 1:
            item = self.get_argument(context, cls=cls)
            if item is None:
                return
            timezone = getattr(context, 'timezone', None)
        else:
            item = self.get_argument(context, cls=cls)
            timezone = self.get_argument(context, 1, cls=DayTimeDuration)

            if timezone is not None:
                try:
                    timezone = Timezone.fromduration(timezone)
                except ValueError as err:
                    raise self.error('FODT0003', str(err)) from None
            if item is None:
                return

        try:
            if item.tzinfo is not None and timezone is not None:
                if isinstance(item, DateTime10):
                    item += timezone.offset
                elif not isinstance(item, Date10):
                    item += timezone.offset - item.tzinfo.offset
                elif timezone.offset < item.tzinfo.offset:
                    item -= timezone.offset - item.tzinfo.offset
                    item -= DayTimeDuration.fromstring('P1D')
        except OverflowError as err:
            raise self.error('FODT0001', str(err)) from None

        item.tzinfo = timezone
        return item

    @contextlib.contextmanager
    def use_locale(self, collation):
        """A context manager for use a locale setting for string comparison in a code block."""
        loc = locale.getlocale(locale.LC_COLLATE)
        if collation == UNICODE_CODEPOINT_COLLATION:
            collation = 'en_US.UTF-8'
        elif collation is None:
            raise self.error('XPTY0004', 'collation cannot be an empty sequence')

        try:
            locale.setlocale(locale.LC_COLLATE, collation)
        except locale.Error:
            raise self.error('FOCH0002', 'Unsupported collation %r' % collation) from None
        else:
            yield
        finally:
            locale.setlocale(locale.LC_COLLATE, loc)

    ###
    # XSD types related methods
    def select_xsd_nodes(self, schema_context, name):
        """
        Selector for XSD nodes (elements, attributes and schemas). If there is
        a match with an attribute or an element the node's type is added to
        matching types of the token. For each matching elements or attributes
        yields tuple nodes containing the node, its type and a compatible value
        for doing static evaluation. For matching schemas yields the original
        instance.

        :param schema_context: an XPathSchemaContext instance.
        :param name: a QName in extended format.
        """
        for xsd_node in schema_context.iter_children_or_self():
            if xsd_node is None:
                if name == schema_context.root.tag == '{%s}schema' % XSD_NAMESPACE:
                    yield None
                continue

            try:
                if isinstance(xsd_node, AttributeNode):
                    if xsd_node.value.is_matching(name):
                        if xsd_node.name is None:
                            # node is an XSD attribute wildcard
                            xsd_node = self.parser.schema.get_attribute(name)
                            if xsd_node is None:
                                continue

                        xsd_type = self.add_xsd_type(xsd_node)
                        value = self.parser.get_atomic_value(xsd_type)
                        yield TypedAttribute(xsd_node, xsd_type, value)

                elif xsd_node.is_matching(name, self.parser.default_namespace):
                    if xsd_node.name is None:
                        # node is an XSD element wildcard
                        xsd_node = self.parser.schema.get_element(name)
                        if xsd_node is None:
                            continue

                    xsd_type = self.add_xsd_type(xsd_node)
                    value = self.parser.get_atomic_value(xsd_type)
                    yield TypedElement(xsd_node, xsd_type, value)

            except AttributeError:
                # Item is a schema
                if name == xsd_node.tag == '{%s}schema' % XSD_NAMESPACE:
                    yield xsd_node

    def add_xsd_type(self, item):
        """
        Adds an XSD type association from an item. The association is
        added using the item's name and type.
        """
        if isinstance(item, AttributeNode):
            item = item.value
        elif isinstance(item, TypedAttribute):
            item = item[0].value
        elif isinstance(item, TypedElement):
            item = item[0]

        if not is_schema_node(item):
            return

        if self.xsd_types is None:
            self.xsd_types = {item.name: item.type}
        else:
            obj = self.xsd_types.get(item.name)
            if obj is None:
                self.xsd_types[item.name] = item.type
            elif not isinstance(obj, list):
                if obj is not item.type:
                    self.xsd_types[item.name] = [obj, item.type]
            elif item.type not in obj:
                obj.append(item.type)

        return item.type

    def get_xsd_type(self, item):
        """
        Returns the XSD type associated with an item. Match by item's name
        and XSD validity. Returns `None` if no XSD type is matching.

        :param item: a string or an AttributeNode or an element.
        """
        if not self.xsd_types or isinstance(self.xsd_types, AbstractSchemaProxy):
            return
        elif isinstance(item, str):
            xsd_type = self.xsd_types.get(item)
        elif isinstance(item, AttributeNode):
            xsd_type = self.xsd_types.get(item.name)
        elif isinstance(item, (TypedAttribute, TypedElement)):
            return item.type
        else:
            xsd_type = self.xsd_types.get(item.tag)

        if not xsd_type:
            return
        elif not isinstance(xsd_type, list):
            return xsd_type
        elif isinstance(item, AttributeNode):
            for x in xsd_type:
                if x.is_valid(item.value):
                    return x
        elif not isinstance(item, str):
            for x in xsd_type:
                if x.is_simple():
                    if x.is_valid(item.text):
                        return x
                elif x.is_valid(item):
                    return x

        return xsd_type[0]

    def get_typed_node(self, item):
        """
        Returns a typed node if the item is matching an XSD type.

        Ref:
          https://www.w3.org/TR/xpath20/#id-processing-model
          https://www.w3.org/TR/xpath20/#id-static-analysis
          https://www.w3.org/TR/xquery-semantics/

        :param item: an untyped attribute ot element.
        :return: a TypedAttribute or a TypedElement, or the argument \
        if it's not matching any associated XSD type.
        """
        if isinstance(item, (TypedAttribute, TypedElement)):
            return item

        xsd_type = self.get_xsd_type(item)
        if not xsd_type:
            return item
        elif xsd_type.name in XSD_SPECIAL_TYPES:
            if isinstance(item, AttributeNode):
                return TypedAttribute(item, xsd_type, UntypedAtomic(item.value))
            return TypedElement(item, xsd_type, UntypedAtomic(item.text or ''))

        elif xsd_type.has_mixed_content():
            value = UntypedAtomic(item.text or '')
            return TypedElement(item, xsd_type, value)
        elif xsd_type.is_element_only():
            return TypedElement(item, xsd_type, None)
        elif xsd_type.is_empty():
            return TypedElement(item, xsd_type, None)

        if self.parser.xsd_version == '1.0':
            atomic_types = xsd10_atomic_types
        else:
            atomic_types = xsd11_atomic_types

        try:
            builder = atomic_types[xsd_type.name]
        except KeyError:
            pass
        else:
            if issubclass(builder, (AbstractDateTime, Duration)):
                builder = builder.fromstring
            elif issubclass(builder, QName):
                builder = self.cast_to_qname

            try:
                if isinstance(item, AttributeNode):
                    return TypedAttribute(item, xsd_type, builder(item.value))
                else:
                    return TypedElement(item, xsd_type, builder(item.text))
            except (TypeError, ValueError):
                msg = "Type {!r} does not match sequence type of {!r}"
                raise self.wrong_sequence_type(msg.format(xsd_type, item)) from None

        try:
            primitive_type = self.parser.schema.get_primitive_type(xsd_type)
            builder = atomic_types[primitive_type.name]
        except (KeyError, AttributeError):
            builder = UntypedAtomic
        else:
            if isinstance(builder, (AbstractDateTime, Duration)):
                builder = builder.fromstring
            elif issubclass(builder, QName):
                builder = self.cast_to_qname

        try:
            if isinstance(item, AttributeNode):
                if xsd_type.is_valid(item[1]):
                    return TypedAttribute(item, xsd_type, builder(item[1]))
            elif xsd_type.is_valid(item.text):
                return TypedElement(item, xsd_type, builder(item.text))
        except (TypeError, ValueError):
            pass

        msg = "Type {!r} does not match sequence type of {!r}"
        raise self.wrong_sequence_type(msg.format(xsd_type, item)) from None

    def cast_to_qname(self, qname):
        """Cast a prefixed qname string to a QName object."""
        try:
            if ':' not in qname:
                return QName(self.parser.namespaces.get(''), qname.strip())
            pfx, _ = qname.strip().split(':')
            return QName(self.parser.namespaces[pfx], qname)
        except ValueError:
            msg = 'invalid value {!r} for an xs:QName'.format(qname.strip())
            raise self.error('FORG0001', msg)
        except KeyError as err:
            raise self.error('FONS0004', 'no namespace found for prefix {}'.format(err))

    def cast_to_double(self, value):
        """Cast a value to xs:double."""
        try:
            if self.parser.xsd_version == '1.0':
                return DoubleProxy10(value)
            return DoubleProxy(value)
        except ValueError as err:
            raise self.error('FORG0001', str(err))  # str or UntypedAtomic

    ###
    # XPath data accessors base functions
    def boolean_value(self, obj):
        """
        The effective boolean value, as computed by fn:boolean().
        """
        if isinstance(obj, list):
            if not obj:
                return False
            elif is_xpath_node(obj[0]):
                return True
            elif len(obj) > 1:
                message = "effective boolean value is not defined for a sequence " \
                          "of two or more items not starting with an XPath node."
                raise self.error('FORG0006', message)
            else:
                obj = obj[0]

        if isinstance(obj, (int, str, UntypedAtomic, AnyURI)):  # Include bool
            return bool(obj)
        elif isinstance(obj, (float, Decimal)):
            return False if math.isnan(obj) else bool(obj)
        elif obj is None:
            return False
        else:
            message = "effective boolean value is not defined for {!r}.".format(type(obj))
            raise self.error('FORG0006', message)

    def data_value(self, obj):
        """
        The typed value, as computed by fn:data() on each item.
        Returns an instance of UntypedAtomic for untyped data.

        https://www.w3.org/TR/xpath20/#dt-typed-value
        """
        if obj is None:
            return
        elif isinstance(obj, (tuple, XPathNode)):
            if isinstance(obj, (AttributeNode, TextNode)):
                return UntypedAtomic(obj.value)
            elif isinstance(obj, (TypedElement, TypedAttribute)):
                return obj.value
            elif isinstance(obj, NamespaceNode):
                return obj.uri
            else:
                raise RuntimeError("invalid argument {!r} for fn:data".format(obj))

        elif is_schema_node(obj):
            return self.parser.get_atomic_value(obj.type)
        elif hasattr(obj, 'tag'):
            if is_comment_node(obj):
                return obj.text
            elif is_processing_instruction_node(obj):
                return obj.text
            elif hasattr(obj, 'attrib') and hasattr(obj, 'text'):
                return UntypedAtomic(''.join(etree_iter_strings(obj)))
        elif is_document_node(obj):
            value = ''.join(etree_iter_strings(obj.getroot()))
            return UntypedAtomic(value)
        else:
            return obj

    def string_value(self, obj):
        """
        The string value, as computed by fn:string().
        """
        if obj is None:
            return ''
        elif isinstance(obj, (tuple, XPathNode)):
            if isinstance(obj, TypedElement):
                if obj.value is None:
                    return ''.join(etree_iter_strings(obj))
                return str(obj.value)
            elif isinstance(obj, (AttributeNode, TypedAttribute)):
                return str(obj.value)
            elif isinstance(obj, TextNode):
                return obj.value
            elif isinstance(obj, NamespaceNode):
                return obj.uri
        elif is_schema_node(obj):
            return str(self.parser.get_atomic_value(obj.type))
        elif hasattr(obj, 'tag'):
            if is_comment_node(obj):
                return obj.text
            elif is_processing_instruction_node(obj):
                return obj.text
            elif hasattr(obj, 'attrib') and hasattr(obj, 'text'):
                return ''.join(etree_iter_strings(obj))
        elif is_document_node(obj):
            return ''.join(etree_iter_strings(obj.getroot()))
        elif isinstance(obj, bool):
            return 'true' if obj else 'false'
        elif isinstance(obj, Decimal):
            value = format(obj, 'f')
            if '.' in value:
                return value.rstrip('0').rstrip('.')
            return value

        elif isinstance(obj, float):
            if math.isnan(obj):
                return 'NaN'
            elif math.isinf(obj):
                return str(obj).upper()

            value = str(obj)
            if '.' in value:
                value = value.rstrip('0').rstrip('.')
            if '+' in value:
                value = value.replace('+', '')
            if 'e' in value:
                return value.upper()
            return value

        return str(obj)

    def number_value(self, obj):
        """
        The numeric value, as computed by fn:number() on each item. Returns a float value.
        """
        try:
            return float(self.string_value(obj) if is_xpath_node(obj) else obj)
        except (TypeError, ValueError):
            return float('nan')

    ###
    # Error handling helpers
    def error_code(self, code):
        """Returns a prefixed error code."""
        if self.parser.namespaces.get('err') == XQT_ERRORS_NAMESPACE:
            return 'err:%s' % code

        for pfx, uri in self.parser.namespaces.items():
            if uri == XQT_ERRORS_NAMESPACE:
                return '%s:%s' % (pfx, code) if pfx else code

        return code  # returns an unprefixed code (without prefix the namespace is not checked)

    def error(self, code, message_or_error=None):
        """
        Returns an XPath error instance related with a code. An XPath/XQuery/XSLT error code is an
        alphanumeric token starting with four uppercase letters and ending with four digits.

        :param code: the error code as QName or string.
        :param message_or_error: an optional custom additional message.
        """
        if isinstance(code, QName):
            code, namespace = code.local_name, code.uri
        elif ':' not in code:
            namespace = None
        else:
            try:
                prefix, code = code.split(':')
            except ValueError:
                raise ElementPathValueError(
                    message='%r is not a prefixed name' % code,
                    code=self.error_code('XPTY0004'),
                    token=self,
                )
            else:
                namespace = self.parser.namespaces.get(prefix)

        if namespace and namespace != XQT_ERRORS_NAMESPACE:
            raise ElementPathValueError(
                message='%r namespace is required' % XQT_ERRORS_NAMESPACE,
                code=self.error_code('XPTY0004'),
                token=self,
            )

        try:
            error_class, default_message = XPATH_ERROR_CODES[code]
        except KeyError:
            raise ElementPathValueError(
                message='unknown XPath error code %r' % code,
                code=self.error_code('XPTY0004'),
                token=self,
            )

        if message_or_error is None:
            message = default_message
        elif isinstance(message_or_error, str):
            message = message_or_error
        elif isinstance(message_or_error, ElementPathError):
            message = message_or_error.message
        else:
            message = str(message_or_error)

        return error_class(message, code=self.error_code(code), token=self)

    # Shortcuts for XPath errors, only the wrong_syntax
    def expected(self, *symbols, message=None, code='XPST0003'):
        if symbols and self.symbol not in symbols:
            raise self.wrong_syntax(message, code)

    def unexpected(self, *symbols, message=None, code='XPST0003'):
        if not symbols or self.symbol in symbols:
            raise self.wrong_syntax(message, code)

    def wrong_syntax(self, message=None, code='XPST0003'):
        if self.label == 'function':
            code = 'XPST0017'

        if message:
            return self.error(code, message)

        error = super(XPathToken, self).wrong_syntax(message)
        return self.error(code, str(error))

    def wrong_value(self, message=None):
        return self.error('FOCA0002', message)

    def wrong_type(self, message=None):
        return self.error('FORG0006', message)

    def missing_schema(self, message=None):
        return self.error('XPST0001', message)

    def missing_context(self, message=None):
        return self.error('XPDY0002', message)

    def wrong_context_type(self, message=None):
        return self.error('XPTY0004', message)

    def missing_sequence(self, message=None):
        return self.error('XPST0005', message)

    def missing_name(self, message=None):
        return self.error('XPST0008', message)

    def missing_axis(self, message=None):
        if self.parser.compatibility_mode:
            return self.error('XPST0010', message)
        return self.error('XPST0003', message)

    def wrong_nargs(self, message=None):
        return self.error('XPST0017', message)

    def wrong_step_result(self, message=None):
        return self.error('XPTY0018', message)

    def wrong_intermediate_step_result(self, message=None):
        return self.error('XPTY0019', message)

    def wrong_axis_argument(self, message=None):
        return self.error('XPTY0020', message)

    def wrong_sequence_type(self, message=None):
        return self.error('XPDY0050', message)

    def unknown_atomic_type(self, message=None):
        return self.error('XPST0051', message)

    def wrong_target_type(self, message=None):
        return self.error('XPST0080', message)

    def unknown_namespace(self, message=None):
        return self.error('XPST0081', message)
