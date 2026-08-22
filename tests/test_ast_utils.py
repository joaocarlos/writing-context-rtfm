import unittest
from unittest.mock import MagicMock

from writing_context_rtfm.ast_utils import get_braced_arg, get_clean_arg_text


class TestAstUtils(unittest.TestCase):
    def test_get_clean_arg_text_none(self):
        self.assertEqual(get_clean_arg_text(None), "")

    def test_get_clean_arg_text_no_nodelist_with_latex_verbatim_braced(self):
        mock_node = MagicMock(spec=["latex_verbatim"])
        mock_node.latex_verbatim.return_value = "{sample text}"
        self.assertEqual(get_clean_arg_text(mock_node), "sample text")

    def test_get_clean_arg_text_no_nodelist_with_latex_verbatim_unbraced(self):
        mock_node = MagicMock(spec=["latex_verbatim"])
        mock_node.latex_verbatim.return_value = "raw text"
        self.assertEqual(get_clean_arg_text(mock_node), "raw text")

    def test_get_clean_arg_text_no_nodelist_no_verbatim(self):
        mock_node = object()
        self.assertEqual(get_clean_arg_text(mock_node), "")

    def test_get_clean_arg_text_with_nodelist(self):
        # Child 1: chars node
        child1 = MagicMock()
        child1.isNodeType.return_value = True
        child1.chars = "Hello "

        # Child 2: verbatim node
        child2 = MagicMock()
        child2.isNodeType.return_value = False
        child2.latex_verbatim.return_value = "World"

        # Child 3: None child
        child3 = None

        group = MagicMock()
        group.nodelist = [child1, child2, child3]

        self.assertEqual(get_clean_arg_text(group), "Hello World")

    def test_get_braced_arg_no_nodeargs(self):
        node = object()
        self.assertIsNone(get_braced_arg(node))

    def test_get_braced_arg_with_matching_delimiter(self):
        arg1 = MagicMock()
        arg1.delimiters = ("[", "]")

        arg2 = MagicMock()
        arg2.delimiters = ("{", "}")
        arg2.nodelist = None
        arg2.latex_verbatim.return_value = "{methodology}"

        node = MagicMock()
        node.nodeargs = [arg1, arg2]

        self.assertEqual(get_braced_arg(node), "methodology")

    def test_get_braced_arg_no_matching_delimiter(self):
        arg1 = MagicMock()
        arg1.delimiters = ("[", "]")

        node = MagicMock()
        node.nodeargs = [arg1]

        self.assertIsNone(get_braced_arg(node))
