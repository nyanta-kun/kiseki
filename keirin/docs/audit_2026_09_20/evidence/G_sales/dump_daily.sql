COPY (SELECT * FROM keirin.netkeirin_sales_daily ORDER BY sale_date) TO STDOUT WITH CSV HEADER;
