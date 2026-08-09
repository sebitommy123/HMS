CREATE INDEX IF NOT EXISTS sec_financial_sub_adsh_idx ON sec_financial.sub (adsh);
CREATE INDEX IF NOT EXISTS sec_financial_sub_cik_idx ON sec_financial.sub (cik);
CREATE INDEX IF NOT EXISTS sec_financial_num_adsh_idx ON sec_financial.num (adsh);
CREATE INDEX IF NOT EXISTS sec_financial_num_tag_version_idx ON sec_financial.num (tag, version);
CREATE INDEX IF NOT EXISTS sec_financial_tag_tag_version_idx ON sec_financial.tag (tag, version);
CREATE INDEX IF NOT EXISTS sec_financial_pre_adsh_idx ON sec_financial.pre (adsh);
CREATE INDEX IF NOT EXISTS sec_financial_pre_tag_version_idx ON sec_financial.pre (tag, version);

CREATE INDEX IF NOT EXISTS sec_form345_submission_accession_idx ON sec_form345.submission (accession_number);
CREATE INDEX IF NOT EXISTS sec_form345_submission_issuer_cik_idx ON sec_form345.submission (issuercik);
CREATE INDEX IF NOT EXISTS sec_form345_reportingowner_accession_idx ON sec_form345.reportingowner (accession_number);
CREATE INDEX IF NOT EXISTS sec_form345_reportingowner_owner_cik_idx ON sec_form345.reportingowner (rptownercik);
CREATE INDEX IF NOT EXISTS sec_form345_nonderiv_trans_accession_idx ON sec_form345.nonderiv_trans (accession_number);
CREATE INDEX IF NOT EXISTS sec_form345_deriv_trans_accession_idx ON sec_form345.deriv_trans (accession_number);
CREATE INDEX IF NOT EXISTS sec_form345_footnotes_accession_idx ON sec_form345.footnotes (accession_number);

CREATE INDEX IF NOT EXISTS sec_form13f_submission_accession_idx ON sec_form13f.submission (accession_number);
CREATE INDEX IF NOT EXISTS sec_form13f_submission_cik_idx ON sec_form13f.submission (cik);
CREATE INDEX IF NOT EXISTS sec_form13f_coverpage_accession_idx ON sec_form13f.coverpage (accession_number);
CREATE INDEX IF NOT EXISTS sec_form13f_infotable_accession_idx ON sec_form13f.infotable (accession_number);
CREATE INDEX IF NOT EXISTS sec_form13f_infotable_cusip_idx ON sec_form13f.infotable (cusip);
CREATE INDEX IF NOT EXISTS sec_form13f_infotable_issuer_idx ON sec_form13f.infotable (nameofissuer);
CREATE INDEX IF NOT EXISTS sec_form13f_summarypage_accession_idx ON sec_form13f.summarypage (accession_number);

ANALYZE;
