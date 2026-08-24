-- Migration: remove html_file_path and env_html_file_path from web_pages.
-- These paths are derivable from page_path and were never used for querying.
-- Run against an existing database:
--   docker exec -i synagogue_db psql -U research synagogues < migrate_drop_html_paths.sql

ALTER TABLE web_pages DROP COLUMN IF EXISTS html_file_path;
ALTER TABLE web_pages DROP COLUMN IF EXISTS env_html_file_path;
