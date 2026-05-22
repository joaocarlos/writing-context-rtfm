"""Test basic module imports to ensure the scaffold is correct."""

def test_imports():
    import writing_context_rtfm
    import writing_context_rtfm.cli
    import writing_context_rtfm.config
    import writing_context_rtfm.rtfm_adapter
    import writing_context_rtfm.section_cards
    import writing_context_rtfm.context_pack
    import writing_context_rtfm.storage
    import writing_context_rtfm.hashing
    import writing_context_rtfm.server
    import writing_context_rtfm.schemas
    import writing_context_rtfm.proofread
    import writing_context_rtfm.utils
    import writing_context_rtfm.token_budget

    assert writing_context_rtfm.__version__ == "0.5.0"
    print("All imports successful! Basic tests passed.")

if __name__ == "__main__":
    test_imports()
