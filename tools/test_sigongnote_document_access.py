"""Offline checks that access failure cannot become verified procurement evidence."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fetch_sigongnote_priority_documents as fetch
from finalize_sigongnote_document_review import case_status


class DocumentAccess(unittest.TestCase):
    def test_binary_and_html_signatures(self):
        self.assertEqual(fetch.file_kind(b'%PDF-1.7'),'pdf')
        self.assertEqual(fetch.file_kind(bytes.fromhex('D0CF11E0A1B11AE1')),'compound')
        self.assertEqual(fetch.file_kind(b'PK\x03\x04'),'zip')
        self.assertEqual(fetch.file_kind(b'<!DOCTYPE html><html>Error</html>'),'html')
        self.assertEqual(fetch.file_kind(b''),'unknown')
    def test_filename_metadata_not_path_execution(self):
        self.assertEqual(fetch.filename("attachment; filename*=UTF-8''test%20notice.pdf"),'test notice.pdf')
        self.assertEqual(fetch.filename(''),'')
    def test_failure_and_unattempted_are_different(self):
        s=case_status([{'fetch_status':'FAILED'},{'fetch_status':'SKIPPED_NETWORK_BLOCK'}])
        self.assertEqual((s['failed'],s['skipped'],s['downloaded']),(1,1,0))
    def test_no_attempt_not_no_data(self):
        s=case_status([{'fetch_status':'NOT_ATTEMPTED'}])
        self.assertEqual(s['failed'],0);self.assertIn('미시도',s['reason'])
    def test_reserved_without_result_never_retried_or_success(self):
        plan=[dict(document_id='synthetic',priority_id='P00')]
        ledger=[dict(event='reserved',document_id='synthetic',priority_id='P00')]
        with tempfile.TemporaryDirectory() as tmp,patch.object(fetch,'OUT',Path(tmp)):
            summary=fetch.export(plan,ledger)
            self.assertEqual(summary['attempts'],1);self.assertEqual(summary['downloaded_files'],0)
            self.assertEqual(summary['statuses'],{'INTERRUPTED_NO_RETRY':1})
    def test_three_transport_failures_mark_other_links_skipped(self):
        plan=[dict(document_id=str(i),priority_id='P00') for i in range(4)]
        ledger=[]
        for i in range(3):
            ledger.extend([dict(event='reserved',document_id=str(i)),dict(event='result',document_id=str(i),fetch_status='FAILED',reason='NETWORK_CONNECT_FAILURE')])
        with tempfile.TemporaryDirectory() as tmp,patch.object(fetch,'OUT',Path(tmp)):
            summary=fetch.export(plan,ledger)
            self.assertEqual(summary['statuses'],{'FAILED':3,'SKIPPED_NETWORK_BLOCK':1})
            self.assertEqual(summary['attempts'],3)
    def test_web_byte_count_explicitly_unobserved(self):
        plan=[dict(document_id='demo',priority_id='P00')]
        ledger=[dict(event='reserved',document_id='demo',tool='web_read'),dict(event='result',document_id='demo',fetch_status='FAILED',reason='WEB_TOOL_URL_NOT_ACCESSIBLE',transfer_bytes_observed=False)]
        with tempfile.TemporaryDirectory() as tmp,patch.object(fetch,'OUT',Path(tmp)):
            summary=fetch.export(plan,ledger)
            self.assertEqual(summary['web_read_attempts'],1);self.assertEqual(summary['httpx_attempts'],0)
            with (Path(tmp)/'attachment_fetch_results.csv').open(encoding='utf-8-sig',newline='') as f:row=next(csv.DictReader(f))
            self.assertEqual(row['transfer_bytes_observed'],'False');self.assertEqual(row['http_status'],'')


if __name__=='__main__':unittest.main(verbosity=2)
