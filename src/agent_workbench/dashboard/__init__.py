"""Local, read-only Streamlit dashboard for agent-workbench projects.

The data layer (``loader``, ``analysis``, ``fmt``, ``messages``) imports without
streamlit. Only ``views`` and ``app`` need it (``pip install 'agent-workbench[dashboard]'``).
Run: ``python -m agent_workbench.dashboard [--project PATH] [--workspace FILE]``.
"""
