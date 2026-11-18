-- Wrenfield's analysts query the S3 Parquet lake in place with DuckDB (an Athena stand-in).
-- On AWS: INSTALL httpfs; LOAD httpfs; CREATE SECRET (TYPE s3, PROVIDER credential_chain);
-- and the path is s3://wrenfield-data-lake/curated/... The tests substitute {lake} with a
-- directory.

-- Latest OAS by rating bucket, high yield only.
SELECT CASE WHEN oas_bp < 300 THEN 'tight' WHEN oas_bp < 600 THEN 'mid' ELSE 'wide' END AS bucket,
       count(*) AS bonds, avg(oas_bp) AS mean_oas_bp
FROM read_parquet('{lake}/curated/bond_px_daily/dt=*/part-0000.parquet', hive_partitioning = true)
WHERE dt = (SELECT max(dt) FROM read_parquet('{lake}/curated/bond_px_daily/dt=*/part-0000.parquet',
                                             hive_partitioning = true))
  AND oas_bp IS NOT NULL
GROUP BY 1 ORDER BY 1;
