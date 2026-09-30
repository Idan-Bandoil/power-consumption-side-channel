# Battery-vs-RAPL cross-validation (critique B1.2). analysis.battery is the only
# reader for a kind:"battery" run -- its CSV schema (round,cond,arm_s,rapl_w,
# batt_w,n_batt) is not the driver's, so analysis.report/aggregate do not apply.
./venv/bin/python3 -m analysis.battery "$r"
