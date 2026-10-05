"""Focused task-43 acceptance with a spawn-safe entry point and separate logs."""
import sys
import unittest


def main():
    import test_query_sources as sources
    from virtual_fixtures import REPO
    sources.EVIDENCE=REPO/'result/virtual-tables-completion/previous-contracts'
    sources.EVIDENCE.mkdir(parents=True,exist_ok=True)
    names=sys.argv[1:] or ['test_virtual_extensions',
           'test_query_sources.SourceDockerTest.test_two_portable_projects_semantics_in_script',
           'test_query_sources.SourcePublicTest.test_two_fresh_isolated_batches',
           'test_query_composites.CompositeEdgeTest.test_hidden_null_column_collision_and_nullable_alias_comparison']
    suite=unittest.defaultTestLoader.loadTestsFromNames(names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return int(not result.wasSuccessful())


if __name__=='__main__':raise SystemExit(main())
