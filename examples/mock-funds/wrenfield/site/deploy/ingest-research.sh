#!/bin/sh
# An operator ingests the research bucket into the knowledge store (ingestion is an operator
# command, never a tool). Run as a one-off ECS task with the `qaos_ingest` database role.
# Not run in tests.
set -eu
mkdir -p /tmp/corpus
aws s3 sync s3://wrenfield-research/credit/ /tmp/corpus/ --only-show-errors
qaos ingest /tmp/corpus --store "postgresql://qaos_ingest@wrenfield-kb.cluster-c1x2y3.us-east-1.rds.amazonaws.com:5432/kb?sslmode=verify-full"
