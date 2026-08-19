"""Test basic module imports to ensure the scaffold is correct."""


def test_imports():
    import re

    import writing_context_rtfm
    import writing_context_rtfm.cli
    import writing_context_rtfm.config
    import writing_context_rtfm.context_pack
    import writing_context_rtfm.hashing
    import writing_context_rtfm.proofread
    import writing_context_rtfm.rtfm_adapter
    import writing_context_rtfm.schemas
    import writing_context_rtfm.section_cards
    import writing_context_rtfm.server
    import writing_context_rtfm.storage
    import writing_context_rtfm.token_budget
    import writing_context_rtfm.utils

    assert hasattr(writing_context_rtfm, "__version__")
    assert isinstance(writing_context_rtfm.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", writing_context_rtfm.__version__)
    print(f"All imports successful! Package version: {writing_context_rtfm.__version__}")


if __name__ == "__main__":
    test_imports()
