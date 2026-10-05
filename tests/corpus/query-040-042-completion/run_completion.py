"""Focused repeatable acceptance; preserve original task receipts."""
import sys
import unittest
from pathlib import Path

def main():
    import test_query_joins as joins
    import test_query_projections as projections
    import test_query_composites as composites
    import test_query_results as results
    import test_query_state as state
    from query_completion_fixtures import OUT
    for label,module in [('040',joins),('041',projections),('042',composites),('044',results),('045',state)]:
        module.EVIDENCE=OUT/('fresh-'+label);module.EVIDENCE.mkdir(parents=True,exist_ok=True)
    names=sys.argv[1:] or ['test_query_completion']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    return int(not result.wasSuccessful())

if __name__=='__main__':raise SystemExit(main())
