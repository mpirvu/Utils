#!/usr/bin/env bash
bash -c 'while [[ "$(curl -s -o /dev/null -w ''%{http_code}'' localhost:9090/fruits)" != "200" ]]; do sleep .00001; done'
date +"%H:%M:%S.%N"

