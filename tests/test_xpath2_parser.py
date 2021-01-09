#!/usr/bin/env python
#
# Copyright (c), 2018-2021, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
#
# Note: Many tests are built using the examples of the XPath standards,
#       published by W3C under the W3C Document License.
#
#       References:
#           http://www.w3.org/TR/1999/REC-xpath-19991116/
#           http://www.w3.org/TR/2010/REC-xpath20-20101214/
#           http://www.w3.org/TR/2010/REC-xpath-functions-20101214/
#           https://www.w3.org/Consortium/Legal/2015/doc-license
#           https://www.w3.org/TR/charmod-norm/
#
import unittest
import io
import os
from decimal import Decimal

try:
    import lxml.etree as lxml_etree
except ImportError:
    lxml_etree = None

try:
    import xmlschema
except ImportError:
    xmlschema = None
else:
    xmlschema.XMLSchema.meta_schema.build()

from elementpath import *
from elementpath.datatypes import xsd10_atomic_types, xsd11_atomic_types, DateTime, \
    Date, Time, Timezone, DayTimeDuration, YearMonthDuration, UntypedAtomic, QName
from elementpath.xpath_nodes import node_kind

try:
    from tests import test_xpath1_parser
except ImportError:
    import test_xpath1_parser


def get_sequence_type(value, xsd_version='1.0'):
    """
    Infers the sequence type from a value.
    """
    if value is None or value == []:
        return 'empty-sequence()'
    elif isinstance(value, list):
        if value[0] is not None and not isinstance(value[0], list):
            sequence_type = get_sequence_type(value[0], xsd_version)
            if all(get_sequence_type(x, xsd_version) == sequence_type for x in value[1:]):
                return '{}+'.format(sequence_type)
            else:
                return 'node()+'
    else:
        value_kind = node_kind(value)
        if value_kind is not None:
            return '{}()'.format(value_kind)
        elif isinstance(value, UntypedAtomic):
            return 'xs:untypedAtomic'

        if QName.is_valid(value) and ':' in str(value):
            return 'xs:QName'

        if xsd_version == '1.0':
            atomic_types = xsd10_atomic_types
        else:
            atomic_types = xsd11_atomic_types
            if atomic_types['dateTimeStamp'].is_valid(value):
                return 'xs:dateTimeStamp'

        for type_name in ['string', 'boolean', 'decimal', 'float', 'double',
                          'date', 'dateTime', 'gDay', 'gMonth', 'gMonthDay', 'anyURI',
                          'gYear', 'gYearMonth', 'time', 'duration', 'dayTimeDuration',
                          'yearMonthDuration', 'base64Binary', 'hexBinary']:
            if atomic_types[type_name].is_valid(value):
                return 'xs:%s' % type_name

    raise ValueError("Inconsistent sequence type for {!r}".format(value))


class XPath2ParserTest(test_xpath1_parser.XPath1ParserTest):

    def setUp(self):
        self.parser = XPath2Parser(namespaces=self.namespaces)

        # Make sure the tests are repeatable.
        env_vars_to_tweak = 'LC_ALL', 'LANG'
        self.current_env_vars = {v: os.environ.get(v) for v in env_vars_to_tweak}
        for v in self.current_env_vars:
            os.environ[v] = 'en_US.UTF-8'

    def tearDown(self):
        if hasattr(self, 'current_env_vars'):
            for v in self.current_env_vars:
                if self.current_env_vars[v] is not None:
                    os.environ[v] = self.current_env_vars[v]

    def test_is_sequence_type_method(self):
        self.assertTrue(self.parser.is_sequence_type('empty-sequence()'))
        self.assertTrue(self.parser.is_sequence_type('xs:string'))
        self.assertTrue(self.parser.is_sequence_type('xs:float+'))
        self.assertTrue(self.parser.is_sequence_type('element()*'))
        self.assertTrue(self.parser.is_sequence_type('item()?'))
        self.assertTrue(self.parser.is_sequence_type('xs:untypedAtomic+'))
        self.assertFalse(self.parser.is_sequence_type(10))
        self.assertFalse(self.parser.is_sequence_type(''))
        self.assertFalse(self.parser.is_sequence_type('empty-sequence()*'))
        self.assertFalse(self.parser.is_sequence_type('unknown'))
        self.assertFalse(self.parser.is_sequence_type('unknown?'))
        self.assertFalse(self.parser.is_sequence_type('tns0:unknown'))

    def test_match_sequence_type_method(self):
        self.assertTrue(self.parser.match_sequence_type(None, 'empty-sequence()'))
        self.assertTrue(self.parser.match_sequence_type([], 'empty-sequence()'))
        self.assertFalse(self.parser.match_sequence_type('', 'empty-sequence()'))

        self.assertFalse(self.parser.match_sequence_type('', 'empty-sequence()'))

        root = self.etree.XML('<root><e1/><e2/><e3/></root>')
        self.assertTrue(self.parser.match_sequence_type(root, 'element()'))
        self.assertTrue(self.parser.match_sequence_type([root], 'element()'))
        self.assertTrue(self.parser.match_sequence_type(root, 'element()', '?'))
        self.assertTrue(self.parser.match_sequence_type(root, 'element()', '+'))
        self.assertTrue(self.parser.match_sequence_type(root, 'element()', '*'))
        self.assertFalse(self.parser.match_sequence_type(root[:], 'element()'))
        self.assertFalse(self.parser.match_sequence_type(root[:], 'element()', '?'))
        self.assertTrue(self.parser.match_sequence_type(root[:], 'element()', '+'))
        self.assertTrue(self.parser.match_sequence_type(root[:], 'element()', '*'))

        self.assertTrue(self.parser.match_sequence_type(UntypedAtomic(1), 'xs:untypedAtomic'))
        self.assertFalse(self.parser.match_sequence_type(1, 'xs:untypedAtomic'))

        self.assertTrue(self.parser.match_sequence_type('1', 'xs:string'))
        self.assertFalse(self.parser.match_sequence_type(1, 'xs:string'))
        self.assertFalse(self.parser.match_sequence_type('1', 'xs:unknown'))
        self.assertFalse(self.parser.match_sequence_type('1', 'tns0:string'))

    def test_check_variables_method(self):
        self.parser.variable_types.update(
            (k, get_sequence_type(v)) for k, v in self.variables.items()
        )
        self.assertEqual(self.parser.variable_types,
                         {'values': 'xs:decimal+', 'myaddress': 'xs:string', 'word': 'xs:string'})

        self.assertIsNone(self.parser.check_variables(
            {'values': [1, 2, -1], 'myaddress': 'info@example.com', 'word': ''}
        ))

        with self.assertRaises(NameError) as ctx:
            self.parser.check_variables({'values': 1})
        self.assertIn("[err:XPST0008] missing variable", str(ctx.exception))

        with self.assertRaises(TypeError) as ctx:
            self.parser.check_variables(
                {'values': 1.0, 'myaddress': 'info@example.com', 'word': ''}
            )
        self.assertEqual("[err:XPDY0050] Unmatched sequence type for variable 'values'",
                         str(ctx.exception))

        with self.assertRaises(TypeError) as ctx:
            self.parser.check_variables(
                {'values': 1, 'myaddress': 'info@example.com', 'word': True}
            )
        self.assertEqual("[err:XPDY0050] Unmatched sequence type for variable 'word'",
                         str(ctx.exception))

        self.parser.variable_types.clear()

    def test_xpath_tokenizer(self):
        super(XPath2ParserTest, self).test_xpath_tokenizer()
        self.check_tokenizer("(: this is a comment :)",
                             ['(:', '', 'this', '', 'is', '', 'a', '', 'comment', '', ':)'])
        self.check_tokenizer("last (:", ['last', '', '(:'])

    def test_token_tree(self):
        super(XPath2ParserTest, self).test_token_tree()
        self.check_tree('(1 + 6, 2, 10 - 4)', '(, (, (+ (1) (6)) (2)) (- (10) (4)))')
        self.check_tree('/A/B2 union /A/B1', '(union (/ (/ (A)) (B2)) (/ (/ (A)) (B1)))')
        self.check_tree("//text/(preceding-sibling::text)[1]",
                        '(/ (// (text)) ([ (preceding-sibling (text)) (1)))')

    def test_token_source(self):
        super(XPath2ParserTest, self).test_token_source()
        self.check_source("(5, 6) instance of xs:integer+", '(5, 6) instance of xs:integer+')
        self.check_source("$myaddress treat as element(*, USAddress)",
                          "$myaddress treat as element(*, USAddress)")

    def test_xpath_comments(self):
        self.wrong_syntax("(: this is a comment :)")
        self.check_value("(: this is a comment :) true()", True)
        self.check_value("(: comment 1 :)(: comment 2 :) true()", True)
        self.check_value("(: comment 1 :) true() (: comment 2 :)", True)
        self.wrong_syntax("(: this is a (: nested :) comment :)")
        self.check_value("(: this is a (: nested :) comment :) true()", True)
        self.check_tree('child (: nasty (:nested :) axis comment :) ::B1', '(child (B1))')
        self.check_tree('child (: nasty "(: but not nested :)" axis comment :) ::B1',
                        '(child (B1))')
        self.check_value("5 (: before operator comment :) < 4", False)  # Before infix operator
        self.check_value("5 < (: after operator comment :) 4", False)  # After infix operator
        self.check_value("true (:# nasty function comment :) ()", True)
        self.check_tree(' (: initial comment :)/ (:2nd comment:)A/B1(: 3rd comment :)/ \n'
                        'C1 (: last comment :)\t', '(/ (/ (/ (A)) (B1)) (C1))')

    def test_comma_operator(self):
        self.check_value("1, 2", [1, 2])
        self.check_value("(1, 2)", [1, 2])
        self.check_value("(-9, 28, 10)", [-9, 28, 10])
        self.check_value("(1, 2)", [1, 2])

        root = self.etree.XML('<A/>')
        self.check_selector("(7.0, /A, 'foo')", root, [7.0, root, 'foo'])
        self.check_selector("7.0, /A, 'foo'", root, [7.0, root, 'foo'])
        self.check_selector("/A, 7.0, 'foo'", self.etree.XML('<dummy/>'), [7.0, 'foo'])

    def test_range_expressions(self):
        # Some cases from https://www.w3.org/TR/xpath20/#construct_seq
        self.check_value("1 to 2", [1, 2])
        self.check_value("1 to 10", list(range(1, 11)))
        self.check_value("(10, 1 to 4)", [10, 1, 2, 3, 4])
        self.check_value("10 to 10", [10])
        self.check_value("15 to 10", [])
        self.check_value("fn:reverse(10 to 15)", [15, 14, 13, 12, 11, 10])

    def test_parenthesized_expressions(self):
        self.check_value("(1, 2, '10')", [1, 2, '10'])
        self.check_value("()", [])

    def test_if_expressions(self):
        root = self.etree.XML('<A><B1><C1/><C2/></B1><B2/><B3><C3/><C4/><C5/></B3></A>')
        self.check_value("if (1) then 2 else 3", 2)
        self.check_selector("if (true()) then /A/B1 else /A/B2", root, root[:1])
        self.check_selector("if (false()) then /A/B1 else /A/B2", root, root[1:2])

        # Cases from XPath 2.0 examples
        root = self.etree.XML('<part discounted="false"><wholesale/><retail/></part>')
        self.check_selector(
            'if ($part/@discounted) then $part/wholesale else $part/retail',
            root, [root[0]], variables={'part': root}, variable_types={'part': 'element()'}
        )
        root = self.etree.XML('<widgets>'
                              '  <widget><unit-cost>25</unit-cost></widget>'
                              '  <widget><unit-cost>10</unit-cost></widget>'
                              '  <widget><unit-cost>15</unit-cost></widget>'
                              '</widgets>')
        self.check_selector(
            'if ($widget1/unit-cost < $widget2/unit-cost) then $widget1 else $widget2',
            root, [root[2]], variables={'widget1': root[0], 'widget2': root[2]}
        )

    def test_quantifier_expressions(self):
        # Cases from XPath 2.0 examples
        root = self.etree.XML('<parts>'
                              '  <part discounted="true" available="true" />'
                              '  <part discounted="false" available="true" />'
                              '  <part discounted="true" />'
                              '</parts>')
        self.check_selector("every $part in /parts/part satisfies $part/@discounted", root, True)
        self.check_selector("every $part in /parts/part satisfies $part/@available", root, False)

        root = self.etree.XML('<emps>'
                              '  <employee><salary>1000</salary><bonus>400</bonus></employee>'
                              '  <employee><salary>1200</salary><bonus>300</bonus></employee>'
                              '  <employee><salary>1200</salary><bonus>200</bonus></employee>'
                              '</emps>')
        self.check_selector("some $emp in /emps/employee satisfies "
                            "   ($emp/bonus > 0.25 * $emp/salary)", root, True)
        self.check_selector("every $emp in /emps/employee satisfies "
                            "   ($emp/bonus < 0.5 * $emp/salary)", root, True)

        context = XPathContext(root=self.etree.XML('<dummy/>'))
        self.check_value("some $x in (1, 2, 3), $y in (2, 3, 4) satisfies $x + $y = 4",
                         True, context)
        self.check_value("every $x in (1, 2, 3), $y in (2, 3, 4) satisfies $x + $y = 4",
                         False, context)

        self.check_value("some $x in (1, 2, 3), $y in (2, 3, 4) satisfies $x + $y = 7",
                         True, context)
        self.check_value("some $x in (1, 2, 3), $y in (2, 3, 4) satisfies $x + $y = 8",
                         False, context)

        self.check_value('some $x in (1, 2, "cat") satisfies $x * 2 = 4', True, context)
        self.check_value('every $x in (1, 2, "cat") satisfies $x * 2 = 4', False, context)

    def test_for_expressions(self):
        # Cases from XPath 2.0 examples
        context = XPathContext(root=self.etree.XML('<dummy/>'))
        path = "for $i in (10, 20), $j in (1, 2) return ($i + $j)"
        self.check_value(path, [11, 12, 21, 22], context)
        self.check_source(path, path)

        root = self.etree.XML(
            """
            <bib>
                <book>
                    <title>TCP/IP Illustrated</title>
                    <author>Stevens</author>
                    <publisher>Addison-Wesley</publisher>
                </book>
                <book>
                    <title>Advanced Programming in the Unix Environment</title>
                    <author>Stevens</author>
                    <publisher>Addison-Wesley</publisher>
                </book>
                <book>
                    <title>Data on the Web</title>
                    <author>Abiteboul</author>
                    <author>Buneman</author>
                    <author>Suciu</author>
                </book>
            </bib>
            """)

        # Test step-by-step, testing also other basic features.
        self.check_selector("book/author[1]", root, [root[0][1], root[1][1], root[2][1]])
        self.check_selector("book/author[. = $a]", root, [root[0][1], root[1][1]],
                            variables={'a': 'Stevens'})
        self.check_tree("book/author[. = $a][1]", '(/ (book) ([ ([ (author) (= (.) ($ (a)))) (1)))')
        self.check_selector("book/author[. = $a][1]", root, [root[0][1], root[1][1]],
                            variables={'a': 'Stevens'})
        self.check_selector("book/author[. = 'Stevens'][2]", root, [])

        self.check_selector("for $a in fn:distinct-values(book/author) return $a",
                            root, ['Stevens', 'Abiteboul', 'Buneman', 'Suciu'])

        self.check_selector("for $a in fn:distinct-values(book/author) return book/author[. = $a]",
                            root, [root[0][1], root[1][1]] + root[2][1:4])

        self.check_selector("for $a in fn:distinct-values(book/author) "
                            "return book/author[. = $a][1]",
                            root, [root[0][1], root[1][1]] + root[2][1:4])
        self.check_selector(
            "for $a in fn:distinct-values(book/author) "
            "return (book/author[. = $a][1], book[author = $a]/title)",
            root, [root[0][1], root[1][1], root[0][0], root[1][0], root[2][1],
                   root[2][0], root[2][2], root[2][0], root[2][3], root[2][0]]
        )

    def test_idiv_operator(self):
        self.check_value("5 idiv 2", 2)
        self.check_value("-3.5 idiv -2", 1)
        self.check_value("-3.5 idiv 2", -1)
        self.check_value("-3.5 idiv 0", ZeroDivisionError)
        self.check_value("xs:float('INF') idiv 2", OverflowError)

    def test_comparison_operators(self):
        super(XPath2ParserTest, self).test_comparison_operators()
        self.check_value("0.05 eq 0.05", True)
        self.check_value("19.03 ne 19.02999", True)
        self.check_value("-1.0 eq 1.0", False)
        self.check_value("1 le 2", True)
        self.check_value("3 le 2", False)
        self.check_value("5 ge 9", False)
        self.check_value("5 gt 3", True)
        self.check_value("5 lt 20.0", True)
        self.check_value("false() eq 1", False)
        self.check_value("0 eq false()", True)
        self.check_value("2 * 2 eq 4", True)
        self.check_value("() * 7")
        self.check_value("() * ()")

        self.check_value("() le 4")
        self.check_value("4 gt ()")
        self.check_value("() eq ()")  # Equality of empty sequences is also an empty sequence

    def test_comparison_in_expression(self):
        context = XPathContext(self.etree.XML('<value>false</value>'))
        self.check_value("(. = 'false') = (. = 'false')", True, context)
        self.check_value("(. = 'asdf') != (. = 'false')", True, context)

    def test_boolean_evaluation_in_selector(self):
        context = XPathContext(self.etree.XML("""
        <collection>
            <book>
                <available>true</available>
                <price>10.0</price>
            </book>
            <book>
                <available>1</available>
                <price>10.0</price>
            </book>
            <book>
                <available>false</available>
                <price>5.0</price>
            </book>
            <book>
                <available>0</available>
                <price>5.0</price>
            </book>
        </collection>"""))

        self.check_value("sum(//price)", 30, context)
        self.check_value("sum(//price[../available = 'true'])", 10, context)
        self.check_value("sum(//price[../available = 'false'])", 5, context)
        self.check_value("sum(//price[../available = '1'])", 10, context)
        self.check_value("sum(//price[../available = '0'])", 5, context)
        self.check_value("sum(//price[../available = true()])", 20, context)
        self.check_value("sum(//price[../available = false()])", 10, context)

    def test_comparison_of_sequences(self):
        super(XPath2ParserTest, self).test_comparison_of_sequences()

        self.parser.compatibility_mode = True
        self.wrong_type("(false(), false()) = 1")
        self.check_value("(false(), false()) = (false(), false())", True)
        self.check_value("(false(), false()) = (false(), false(), false())", True)
        self.check_value("(false(), false()) = (false(), true())", True)
        self.check_value("(false(), false()) = (true(), false())", True)
        self.check_value("(false(), false()) = (true(), true())", False)
        self.check_value("(false(), false()) = (true(), true(), false())", True)
        self.parser.compatibility_mode = False

        # From XPath 2.0 examples
        root = self.etree.XML('<collection>'
                              '   <book><author>Kafka</author></book>'
                              '   <book><author>Huxley</author></book>'
                              '   <book><author>Asimov</author></book>'
                              '</collection>')
        context = XPathContext(root=root, variables={'book1': root[0]})
        self.check_value('$book1 / author = "Kafka"', True, context=context)
        self.check_value('$book1 / author eq "Kafka"', True, context=context)

        self.check_value("(1, 2) = (2, 3)", True)
        self.check_value("(2, 3) = (3, 4)", True)
        self.check_value("(1, 2) = (3, 4)", False)
        self.check_value("(1, 2) != (2, 3)", True)  # != is not the inverse of =

        context = XPathContext(root=root, variables={
            'a': UntypedAtomic('1'), 'b': UntypedAtomic('2'), 'c': UntypedAtomic('2.0')
        })
        self.check_value('($a, $b) = ($c, 3.0)', False, context=context)
        self.check_value('($a, $b) = ($c, 2.0)', True, context=context)

        self.wrong_type("(1, 2) le (2, 3)", 'XPTY0004', 'sequence of length greater than one')

        root = self.etree.XML('<root min="10" max="7"/>')
        attributes = [AttributeNode(*x, root) for x in root.attrib.items()]
        self.check_value('@min', [attributes[0]], context=XPathContext(root=root))
        self.check_value('@min le @max', True, context=XPathContext(root=root))
        root = self.etree.XML('<root min="80" max="7"/>')
        self.check_value('@min le @max', False, context=XPathContext(root=root))
        self.check_value('@min le @maximum', None, context=XPathContext(root=root))

        if xmlschema is not None:
            schema = xmlschema.XMLSchema("""
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:element name="root" type="xs:int"/>
                  <xs:complexType name="rootType">
                    <xs:attribute name="min" type="xs:int"/>
                    <xs:attribute name="max" type="xs:int"/>
                  </xs:complexType>
                </xs:schema>""")

            with self.schema_bound_parser(schema.elements['root'].xpath_proxy):
                root = self.etree.XML('<root>11</root>')
                self.check_value('. le 10', False, context=XPathContext(root))
                self.check_value('. le 20', True, context=XPathContext(root))

                root = self.etree.XML('<root>eleven</root>')
                with self.assertRaises(TypeError) as err:
                    self.check_value('. le 10', context=XPathContext(root))
                self.assertIn('XPTY0004', str(err.exception))  # Dynamic context error

                root = self.etree.XML('<value>12</value>')
                with self.assertRaises(TypeError) as err:
                    self.check_value('. le "11"', context=XPathContext(root))
                self.assertIn('XPTY0004', str(err.exception))  # Static schema context error

                with self.assertRaises(TypeError) as err:
                    self.check_value('. le 10', context=XPathContext(root))
                self.assertIn('XPTY0004', str(err.exception))  # Dynamic context error

            schema = xmlschema.XMLSchema("""
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:element name="root" type="xs:anyType"/>
                </xs:schema>""")

            with self.schema_bound_parser(schema.elements['root'].xpath_proxy):
                root = self.etree.XML('<root>15</root>')
                self.check_value('. le "11"', False, context=XPathContext(root))

        root = self.etree.XML('<root><a>1</a><a>10</a><a>30</a><a>50</a></root>')
        self.check_selector("a = (1 to 30)", root, True)
        self.check_selector("a = (2)", root, False)
        self.check_selector("a[1] = (1 to 10, 30)", root, True)
        self.check_selector("a[2] = (1 to 10, 30)", root, True)
        self.check_selector("a[3] = (1 to 10, 30)", root, True)
        self.check_selector("a[4] = (1 to 10, 30)", root, False)

    def test_unknown_axis(self):
        self.wrong_syntax('unknown::node()', 'XPST0003')
        self.wrong_syntax('A/unknown::node()', 'XPST0003')

        self.parser.compatibility_mode = True
        self.wrong_name('unknown::node()', 'XPST0010')
        self.wrong_name('A/unknown::node()', 'XPST0010')
        self.parser.compatibility_mode = False

    def test_predicate(self):
        super(XPath2ParserTest, self).test_predicate()
        root = self.etree.XML('<A><B1><B2/><B2/></B1><C1><C2/><C2/></C1></A>')
        self.check_selector("/(A/*/*)[1]", root, [root[0][0]])
        self.check_selector("/A/*/*[1]", root, [root[0][0], root[1][0]])

    def test_subtract_datetimes(self):
        context = XPathContext(root=self.etree.XML('<A/>'), timezone=Timezone.fromstring('-05:00'))
        self.check_value('xs:dateTime("2000-10-30T06:12:00") - xs:dateTime("1999-11-28T09:00:00Z")',
                         DayTimeDuration.fromstring('P337DT2H12M'), context)
        self.check_value('xs:dateTime("2000-10-30T06:12:00") - xs:dateTime("1999-11-28T09:00:00Z")',
                         DayTimeDuration.fromstring('P336DT21H12M'))

    def test_subtract_dates(self):
        context = XPathContext(root=self.etree.XML('<A/>'), timezone=Timezone.fromstring('Z'))
        self.check_value('xs:date("2000-10-30") - xs:date("1999-11-28")',
                         DayTimeDuration.fromstring('P337D'), context)
        context.timezone = Timezone.fromstring('+05:00')
        self.check_value('xs:date("2000-10-30") - xs:date("1999-11-28Z")',
                         DayTimeDuration.fromstring('P336DT19H'), context)
        self.check_value('xs:date("2000-10-15-05:00") - xs:date("2000-10-10+02:00")',
                         DayTimeDuration.fromstring('P5DT7H'))

        # BCE test cases
        self.check_value('xs:date("0001-01-01") - xs:date("-0001-01-01")',
                         DayTimeDuration.fromstring('P366D'))
        self.check_value('xs:date("-0001-01-01") - xs:date("-0001-01-01")',
                         DayTimeDuration.fromstring('P0D'))
        self.check_value('xs:date("-0001-01-01") - xs:date("0001-01-01")',
                         DayTimeDuration.fromstring('-P366D'))

        self.check_value('xs:date("-0001-01-01") - xs:date("-0001-01-02")',
                         DayTimeDuration.fromstring('-P1D'))
        self.check_value('xs:date("-0001-01-04") - xs:date("-0001-01-01")',
                         DayTimeDuration.fromstring('P3D'))

        self.check_value('xs:date("0200-01-01") - xs:date("-0121-01-01")',
                         DayTimeDuration.fromstring('P116878D'))
        self.check_value('xs:date("-0201-01-01") - xs:date("0120-01-01")',
                         DayTimeDuration.fromstring('-P116877D'))

    def test_subtract_times(self):
        context = XPathContext(root=self.etree.XML('<A/>'), timezone=Timezone.fromstring('-05:00'))
        self.check_value('xs:time("11:12:00Z") - xs:time("04:00:00")',
                         DayTimeDuration.fromstring('PT2H12M'), context)
        self.check_value('xs:time("11:00:00-05:00") - xs:time("21:30:00+05:30")',
                         DayTimeDuration.fromstring('PT0S'), context)
        self.check_value('xs:time("17:00:00-06:00") - xs:time("08:00:00+09:00")',
                         DayTimeDuration.fromstring('PT24H'), context)
        self.check_value('xs:time("24:00:00") - xs:time("23:59:59")',
                         DayTimeDuration.fromstring('-PT23H59M59S'), context)

    def test_add_year_month_duration_to_datetime(self):
        self.check_value('xs:dateTime("2000-10-30T11:12:00") + xs:yearMonthDuration("P1Y2M")',
                         DateTime.fromstring("2001-12-30T11:12:00"))

    def test_add_day_time_duration_to_datetime(self):
        self.check_value('xs:dateTime("2000-10-30T11:12:00") + xs:dayTimeDuration("P3DT1H15M")',
                         DateTime.fromstring("2000-11-02T12:27:00"))

    def test_subtract_year_month_duration_from_datetime(self):
        self.check_value('xs:dateTime("2000-10-30T11:12:00") - xs:yearMonthDuration("P0Y2M")',
                         DateTime.fromstring("2000-08-30T11:12:00"))
        self.check_value('xs:dateTime("2000-10-30T11:12:00") - xs:yearMonthDuration("P1Y2M")',
                         DateTime.fromstring("1999-08-30T11:12:00"))

    def test_subtract_day_time_duration_from_datetime(self):
        self.check_value('xs:dateTime("2000-10-30T11:12:00") - xs:dayTimeDuration("P3DT1H15M")',
                         DateTime.fromstring("2000-10-27T09:57:00"))

    def test_add_year_month_duration_to_date(self):
        self.check_value('xs:date("2000-10-30") + xs:yearMonthDuration("P1Y2M")',
                         Date.fromstring('2001-12-30'))

    def test_subtract_year_month_duration_from_date(self):
        self.check_value('xs:date("2000-10-30") - xs:yearMonthDuration("P1Y2M")',
                         Date.fromstring('1999-08-30'))
        self.check_value('xs:date("2000-02-29Z") - xs:yearMonthDuration("P1Y")',
                         Date.fromstring('1999-02-28Z'))
        self.check_value('xs:date("2000-10-31-05:00") - xs:yearMonthDuration("P1Y1M")',
                         Date.fromstring('1999-09-30-05:00'))

    def test_subtract_day_time_duration_from_date(self):
        self.check_value('xs:date("0001-01-05") - xs:dayTimeDuration("P3DT1H15M")',
                         Date.fromstring('0001-01-01'))
        self.check_value('xs:date("2000-10-30") - xs:dayTimeDuration("P3DT1H15M")',
                         Date.fromstring('2000-10-26'))

    def test_add_day_time_duration_to_time(self):
        self.check_value('xs:time("11:12:00") + xs:dayTimeDuration("P3DT1H15M")',
                         Time.fromstring('12:27:00'))
        self.check_value('xs:time("23:12:00+03:00") + xs:dayTimeDuration("P1DT3H15M")',
                         Time.fromstring('02:27:00+03:00'))

    def test_subtract_day_time_duration_to_time(self):
        self.check_value('xs:time("11:12:00") - xs:dayTimeDuration("P3DT1H15M")',
                         Time.fromstring('09:57:00'))
        self.check_value('xs:time("08:20:00-05:00") - xs:dayTimeDuration("P23DT10H10M")',
                         Time.fromstring('22:10:00-05:00'))

    def test_duration_with_arithmetical_operators(self):
        self.wrong_type('xs:duration("P1Y") * 3', 'XPTY0004', 'unsupported operand type(s)')
        self.wrong_value('xs:duration("P1Y") * xs:float("NaN")', 'FOCA0005')
        self.check_value('xs:duration("P1Y") * xs:float("INF")', OverflowError)
        self.wrong_value('xs:float("NaN") * xs:duration("P1Y")', 'FOCA0005')
        self.check_value('xs:float("INF") * xs:duration("P1Y")', OverflowError)
        self.wrong_type('xs:duration("P3Y") div 3',  'XPTY0004', 'unsupported operand type(s)')

    def test_year_month_duration_operators(self):
        self.check_value('xs:yearMonthDuration("P2Y11M") + xs:yearMonthDuration("P3Y3M")',
                         YearMonthDuration(months=74))
        self.check_value('xs:yearMonthDuration("P2Y11M") - xs:yearMonthDuration("P3Y3M")',
                         YearMonthDuration(months=-4))
        self.check_value('xs:yearMonthDuration("P2Y11M") * 2.3',
                         YearMonthDuration.fromstring('P6Y9M'))
        self.check_value('xs:yearMonthDuration("P2Y11M") div 1.5',
                         YearMonthDuration.fromstring('P1Y11M'))
        self.check_value('xs:yearMonthDuration("P3Y4M") div xs:yearMonthDuration("-P1Y4M")', -2.5)
        self.wrong_value('xs:double("NaN") * xs:yearMonthDuration("P2Y")', 'FOCA0005')
        self.check_value('xs:yearMonthDuration("P1Y") * xs:double("INF")', OverflowError)
        self.wrong_value('xs:yearMonthDuration("P3Y") div xs:double("NaN")', 'FOCA0005')

        self.check_raise('xs:yearMonthDuration("P3Y") div xs:yearMonthDuration("P0Y")',
                         ZeroDivisionError, 'FOAR0001', 'Division by zero')
        self.check_raise('xs:yearMonthDuration("P3Y36M") div 0', OverflowError, 'FODT0002')

    def test_day_time_duration_operators(self):
        self.check_value('xs:dayTimeDuration("P2DT12H5M") + xs:dayTimeDuration("P5DT12H")',
                         DayTimeDuration.fromstring('P8DT5M'))
        self.check_value('xs:dayTimeDuration("P2DT12H") - xs:dayTimeDuration("P1DT10H30M")',
                         DayTimeDuration.fromstring('P1DT1H30M'))
        self.check_value('xs:dayTimeDuration("PT2H10M") * 2.1',
                         DayTimeDuration.fromstring('PT4H33M'))
        self.check_value('xs:dayTimeDuration("P1DT2H30M10.5S") div 1.5',
                         DayTimeDuration.fromstring('PT17H40M7S'))
        self.check_value('3 * xs:dayTimeDuration("P1D")',
                         DayTimeDuration.fromstring('P3D'))
        self.check_value(
            'xs:dayTimeDuration("P2DT53M11S") div xs:dayTimeDuration("P1DT10H")',
            Decimal('1.437834967320261437908496732')
        )

    def test_document_node_accessor(self):
        document = self.etree.parse(io.StringIO('<A/>'))
        context = XPathContext(root=document)
        self.wrong_syntax("document-node(A)")
        self.wrong_syntax("document-node(*)")
        self.wrong_syntax("document-node(true())")
        self.wrong_syntax("document-node(node())")
        self.wrong_type("document-node(element(A), 1)")
        self.check_select("document-node()", [], context)
        self.check_select("self::document-node()", [document], context)
        self.check_selector("self::document-node(element(A))", document, [document])
        self.check_selector("self::document-node(element(B))", document, [])

    def test_element_accessor(self):
        element = self.etree.Element('schema')
        context = XPathContext(root=element)
        self.wrong_syntax("element('name')")
        self.wrong_syntax("element(A, 'name')")
        self.check_select("element()", [], context)
        self.check_select("self::element()", [element], context)
        self.check_select("self::element(schema)", [element], context)
        self.check_select("self::element(schema, xs:string)", [], context)

        root = self.etree.XML('<A a="10">text<B/>tail<B/></A>')
        context = XPathContext(root)
        self.check_select("element(*)", root[:], context)
        self.check_select("element(B)", root[:], context)
        self.check_select("element(A)", [], context)

    def test_attribute_accessor(self):
        root = self.etree.XML('<A a="10" b="20">text<B/>tail<B/></A>')
        context = XPathContext(root)
        self.check_select("attribute()", {'10', '20'}, context)
        self.check_select("attribute(*)", {'10', '20'}, context)
        self.check_select("attribute(a)", ['10'], context)
        self.check_select("attribute(a, xs:int)", ['10'], context)

        if xmlschema is not None:
            schema = xmlschema.XMLSchema("""
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:element name="A" type="AType"/>
                  <xs:complexType name="AType">
                    <xs:attribute name="a" type="xs:int"/>
                    <xs:attribute name="b" type="xs:int"/>
                  </xs:complexType>
                </xs:schema>""")

            with self.schema_bound_parser(schema.elements['A'].xpath_proxy):
                self.check_select("attribute(a, xs:int)", ['10'], context)
                self.check_select("attribute(*, xs:int)", {'10', '20'}, context)
                self.check_select("attribute(a, xs:string)", [], context)
                self.check_select("attribute(*, xs:string)", [], context)

    def test_node_and_node_accessors(self):
        element = self.etree.Element('schema')
        element.attrib.update([('id', '0212349350')])

        context = XPathContext(root=element)
        self.check_select("self::node()", [element], context)
        self.check_select("self::attribute()", ['0212349350'], context)

        context.item = 7
        self.check_select("node()", [], context)
        context.item = 10.2
        self.check_select("node()", [], context)

    def test_union_intersect_except_operators(self):
        root = self.etree.XML('<A><B1><C1/><C2/><C3/></B1><B2><C1/><C2/><C3/><C4/></B2><B3/></A>')
        self.check_selector('/A/B2 union /A/B1', root, root[:2])
        self.check_selector('/A/B2 union /A/*', root, root[:])

        self.check_selector('/A/B2 intersect /A/B1', root, [])
        self.check_selector('/A/B2 intersect /A/*', root, [root[1]])
        self.check_selector('/A/B1/* intersect /A/B2/*', root, [])
        self.check_selector('/A/B1/* intersect /A/*/*', root, root[0][:])

        self.check_selector('/A/B2 except /A/B1', root, root[1:2])
        self.check_selector('/A/* except /A/B2', root, [root[0], root[2]])
        self.check_selector('/A/*/* except /A/B2/*', root, root[0][:])
        self.check_selector('/A/B2/* except /A/B1/*', root, root[1][:])
        self.check_selector('/A/B2/* except /A/*/*', root, [])

        root = self.etree.XML('<root><A/><B/><C/></root>')

        # From variables like XPath 2.0 examples
        context = XPathContext(root, variables={
            'seq1': root[:2],  # (A, B)
            'seq2': root[:2],  # (A, B)
            'seq3': root[1:],  # (B, C)
        })
        self.check_select('$seq1 union $seq2', root[:2], context=context)
        self.check_select('$seq2 union $seq3', root[:], context=context)
        self.check_select('$seq1 intersect $seq2', root[:2], context=context)
        self.check_select('$seq2 intersect $seq3', root[1:2], context=context)
        self.check_select('$seq1 except $seq2', [], context=context)
        self.check_select('$seq2 except $seq3', root[:1], context=context)

    def test_node_comparison_operators(self):
        # Test cases from https://www.w3.org/TR/xpath20/#id-node-comparisons
        root = self.etree.XML('''
        <books>
            <book><isbn>1558604820</isbn><call>QA76.9 C3845</call></book>
            <book><isbn>0070512655</isbn><call>QA76.9 C3846</call></book>
            <book><isbn>0131477005</isbn><call>QA76.9 C3847</call></book>
        </books>''')
        self.check_selector('/books/book[isbn="1558604820"] is /books/book[call="QA76.9 C3845"]',
                            root, True)
        self.check_selector('/books/book[isbn="0070512655"] is /books/book[call="QA76.9 C3847"]',
                            root, False)
        self.check_selector('/books/book[isbn="not a code"] is /books/book[call="QA76.9 C3847"]',
                            root, [])

        root = self.etree.XML('''
        <transactions>
            <purchase><parcel>28-451</parcel></purchase>
            <sale><parcel>33-870</parcel></sale>
            <purchase><parcel>15-392</parcel></purchase>
            <sale><parcel>35-530</parcel></sale>
            <purchase><parcel>10-639</parcel></purchase>
            <purchase><parcel>10-639</parcel></purchase>
            <sale><parcel>39-729</parcel></sale>
        </transactions>''')

        self.check_selector(
            '/transactions/purchase[parcel="28-451"] << /transactions/sale[parcel="33-870"]',
            root, True
        )
        self.check_selector(
            '/transactions/purchase[parcel="15-392"] >> /transactions/sale[parcel="33-870"]',
            root, True
        )
        self.check_selector(
            '/transactions/purchase[parcel="10-639"] >> /transactions/sale[parcel="33-870"]',
            root, TypeError
        )

    def test_empty_sequence_type(self):
        self.check_value("() treat as empty-sequence()", [])
        self.check_value("6 treat as empty-sequence()", TypeError)
        self.wrong_syntax("empty-sequence()")

        context = XPathContext(root=self.etree.XML('<A/>'))
        self.check_value("() instance of empty-sequence()", expected=True, context=context)
        self.check_value(". instance of empty-sequence()", expected=False, context=context)

    def test_item_sequence_type(self):
        self.check_value("4 treat as item()", MissingContextError)

        context = XPathContext(self.etree.XML('<root/>'))
        self.check_value("4 treat as item()", [4], context)
        self.check_value("() treat as item()", TypeError, context)
        self.wrong_syntax("item()")

        context = XPathContext(root=self.etree.XML('<A/>'))
        self.check_value(". instance of item()", expected=True, context=context)
        self.check_value("() instance of item()", expected=False, context=context)

        context = XPathContext(root=self.etree.parse(io.StringIO('<A/>')))
        self.check_value(". instance of item()", expected=True, context=context)
        self.check_value("() instance of item()", expected=False, context=context)

    def test_static_analysis_phase(self):
        context = XPathContext(self.etree.XML('<root/>'), variables=self.variables)
        self.check_value('fn:concat($word, fn:lower-case(" BETA"))', 'alpha beta', context)
        self.check_value('fn:concat($word, fn:lower-case(10))', TypeError, context)
        self.check_value('fn:concat($unknown, fn:lower-case(10))', NameError, context)

    def test_instance_of_expression(self):
        element = self.etree.Element('schema')

        # Test cases from https://www.w3.org/TR/xpath20/#id-instance-of
        self.check_value("5 instance of xs:integer", True)
        self.check_value("5 instance of xs:decimal", True)
        self.check_value("9.0 instance of xs:integer", False)
        self.check_value("(5, 6) instance of xs:integer+", True)

        context = XPathContext(element)
        self.check_value(". instance of element()", True, context)
        context.item = None
        self.check_value(". instance of element()", False, context)

        self.check_value("(5, 6) instance of xs:integer", False)
        self.check_value("(5, 6) instance of xs:integer*", True)
        self.check_value("(5, 6) instance of xs:integer?", False)

        self.check_value("5 instance of empty-sequence()", False)
        self.check_value("() instance of empty-sequence()", True)

    def test_treat_as_expression(self):
        element = self.etree.Element('schema')
        context = XPathContext(element)

        self.check_value("5 treat as xs:integer", [5])
        self.check_value("5 treat as xs:string", ElementPathTypeError)
        self.check_value("5 treat as xs:decimal", [5])
        self.check_value("(5, 6) treat as xs:integer+", [5, 6])
        self.check_value(". treat as element()", [element], context)

        self.check_value("(5, 6) treat as xs:integer", ElementPathTypeError)
        self.check_value("(5, 6) treat as xs:integer*", [5, 6])
        self.check_value("(5, 6) treat as xs:integer?", ElementPathTypeError)

        self.check_value("5 treat as empty-sequence()", ElementPathTypeError)
        self.check_value("() treat as empty-sequence()", [])

    def test_castable_expression(self):
        self.check_value("5 castable as xs:integer", True)
        self.check_value("'5' castable as xs:integer", True)
        self.check_value("'hello' castable as xs:integer", False)
        self.check_value("('5', '6') castable as xs:integer", False)
        self.check_value("() castable as xs:integer", False)
        self.check_value("() castable as xs:integer?", True)

        self.check_value("'NaN' castable as xs:double", True)
        self.check_value("'None' castable as xs:double", False)
        self.check_value("'NaN' castable as xs:float", True)
        self.check_value("'NaN' castable as xs:integer", False)

    def test_cast_expression(self):
        self.check_value("5 cast as xs:integer", 5)
        self.check_value("'5' cast as xs:integer", 5)
        self.check_value("'hello' cast as xs:integer", ValueError)
        self.check_value("('5', '6') cast as xs:integer", TypeError)
        self.check_value("() cast as xs:integer", TypeError)
        self.check_value("() cast as xs:integer?", [])
        self.check_value('"1" cast as xs:boolean', True)
        self.check_value('"0" cast as xs:boolean', False)

    def test_logical_expressions_(self):
        super(XPath2ParserTest, self).test_logical_expressions()

        if xmlschema is not None and xmlschema.__version__ >= '1.2.3':
            schema = xmlschema.XMLSchema("""
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:element name="root">
                    <xs:complexType>
                      <xs:sequence/>
                      <xs:attribute name="a" type="xs:integer"/>      
                      <xs:attribute name="b" type="xs:integer"/>
                    </xs:complexType>
                  </xs:element>
                </xs:schema>""")

            with self.schema_bound_parser(schema.elements['root'].xpath_proxy):
                root_token = self.parser.parse("(@a and not(@b)) or (not(@a) and @b)")
                context = XPathContext(self.etree.XML('<root a="10" b="0"/>'))
                self.assertTrue(root_token.evaluate(context=context) is False)
                context = XPathContext(self.etree.XML('<root a="10" b="1"/>'))
                self.assertTrue(root_token.evaluate(context=context) is False)
                context = XPathContext(self.etree.XML('<root a="10"/>'))
                self.assertTrue(root_token.evaluate(context=context) is True)
                context = XPathContext(self.etree.XML('<root a="0" b="10"/>'))
                self.assertTrue(root_token.evaluate(context=context) is False)
                context = XPathContext(self.etree.XML('<root b="0"/>'))
                self.assertTrue(root_token.evaluate(context=context) is True)

    def test_element_decimal_cast(self):
        root = self.etree.XML('''
        <books>
            <book><isbn>1558604820</isbn><price>12.50</price></book>
            <book><isbn>1558604820</isbn><price>13.50</price></book>
            <book><isbn>1558604820</isbn><price>-0.1</price></book>
        </books>''')
        expected_values = [Decimal('12.5'), Decimal('13.5'), Decimal('-0.1')]
        self.assertEqual(3, len(select(root, "//book")))
        for book in iter_select(root, "//book"):
            context = XPathContext(root=root, item=book)
            root_token = self.parser.parse("xs:decimal(price)")
            self.assertEqual(expected_values.pop(0), root_token.evaluate(context))

    def test_element_decimal_comparison_after_round(self):
        self.check_value('xs:decimal(0.36) = round(0.36*100) div 100', True)

    def test_tokenizer_ambiguity(self):
        # From issue #27
        self.check_tokenizer("sch:pattern[@is-a]", ['sch', ':', 'pattern', '[', '@', 'is-a', ']'])
        self.check_tokenizer("/is-a", ['/', 'is-a'])
        self.check_tokenizer("/-is-a", ['/', '-', 'is-a'])

    def test_token_ambiguity(self):
        # Related to issue #27
        self.check_tokenizer("/is", ['/', 'is'])
        context = XPathContext(self.etree.XML('<root/>'))
        self.check_value('/is', [], context)
        context = XPathContext(self.etree.XML('<is/>'))
        self.check_value('/is', [context.root], context)

    @unittest.skipIf(xmlschema is None, "xmlschema library required.")
    def test_get_atomic_value(self):
        schema = xmlschema.XMLSchema("""
            <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
              <xs:element name="a" type="aType"/>
              <xs:complexType name="aType">
                <xs:sequence>
                  <xs:element name="b1" type="xs:int"/>
                  <xs:element name="b2" type="xs:boolean"/>
                </xs:sequence>
              </xs:complexType>

              <xs:element name="b" type="xs:int"/>
              <xs:element name="c"/>

              <xs:element name="d" type="dType"/>
              <xs:simpleType name="dType">
                <xs:restriction base="xs:float"/>
              </xs:simpleType>

              <xs:element name="e" type="eType"/>
              <xs:simpleType name="eType">
                <xs:union memberTypes="xs:string xs:integer xs:boolean"/>
              </xs:simpleType>
            </xs:schema>""")

        token = self.parser.parse('true()')

        self.assertEqual(self.parser.get_atomic_value('xs:int'), 1)
        self.assertEqual(self.parser.get_atomic_value('xs:unknown'), UntypedAtomic('1'))
        self.assertEqual(self.parser.get_atomic_value(schema.elements['d'].type),
                         UntypedAtomic('1'))

        self.parser.schema = xmlschema.xpath.XMLSchemaProxy()
        try:
            token.string_value(schema.elements['a'].type)
        finally:
            self.parser.schema = None

        self.parser.schema = xmlschema.xpath.XMLSchemaProxy(schema)
        try:
            with self.assertRaises(AttributeError) as err:
                self.parser.get_atomic_value(schema)

            value = self.parser.get_atomic_value('unknown')
            self.assertIsInstance(value, UntypedAtomic)
            self.assertEqual(value, UntypedAtomic(value='1'))

            value = self.parser.get_atomic_value(schema.elements['a'].type)
            self.assertIsInstance(value, UntypedAtomic)
            self.assertEqual(value, UntypedAtomic(value='1'))

            value = self.parser.get_atomic_value(schema.elements['b'].type)
            if not isinstance(value, int):
                import pdb
                pdb.set_trace()
                self.parser.get_atomic_value(schema.elements['b'].type)

            self.assertIsInstance(value, int)
            self.assertEqual(value, 1)

            value = self.parser.get_atomic_value(schema.elements['c'].type)
            self.assertIsInstance(value, UntypedAtomic)
            self.assertEqual(value, UntypedAtomic(value='1'))

            value = self.parser.get_atomic_value(schema.elements['d'].type)
            self.assertIsInstance(value, float)
            self.assertEqual(value, 1.0)

            value = self.parser.get_atomic_value(schema.elements['e'].type)
            self.assertIsInstance(value, UntypedAtomic)
            self.assertEqual(value, UntypedAtomic(value='1'))
        finally:
            self.parser.schema = None


@unittest.skipIf(lxml_etree is None, "The lxml library is not installed")
class LxmlXPath2ParserTest(XPath2ParserTest):
    etree = lxml_etree


if __name__ == '__main__':
    unittest.main()
