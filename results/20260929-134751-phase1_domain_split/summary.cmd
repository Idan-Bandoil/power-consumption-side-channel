./venv/bin/python3 -m analysis.report "$r"
./venv/bin/python3 -m analysis.aggregate "$r"
# B1.3: the core/uncore split is this session's reason to exist. analysis.report
# and analysis.aggregate read only the package column; analysis.domains reads the
# PP0 (core) and PP1 sub-domains the driver now records, and a uniform report+
# aggregate pass would silently drop it -- hence this line, per the summary.cmd rule.
./venv/bin/python3 -m analysis.domains "$r"
