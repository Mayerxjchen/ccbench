"""HPC adapters: the site-owned half of the bench-hpc contract.

The trusted gateway holds an adapter; the Candidate never sees it. An adapter
speaks to the local scheduler (or a stand-in) and maps scheduler state onto the
seven stable operations.
"""
