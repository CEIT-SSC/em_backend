#!/bin/bash

python ../manage.py generate_ts \
  --app_name accounts \
  -a -ec -t -c -ev \
  -o "ts_types/types"

python ../manage.py generate_ts \
  --app_name events \
  -a -ec -t -c -ev \
  -o "ts_types/types"

python ../manage.py generate_ts \
  --app_name shop \
  -a -ec -t -c -ev \
  -o "ts_types/types"

python ../manage.py generate_ts \
  --app_name certificate \
  -a -ec -t -c -ev \
  -o "ts_types/types"

python ../manage.py generate_ts \
  --app_name jobs \
  -a -ec -t -c -ev \
  -o "ts_types/types"
