"""Pure manifest/serialization checks; no renderer, media subprocess, or network."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))
from run_pipeline import json_payload_bytes, validate_expanded_reuse, write


class ExpandedReuseTests(unittest.TestCase):
    def setUp(self):
        self.timeline_sha='1'*64
        self.clean_sha='2'*64
        self.contract={'schema':'timeaigc-proxy-contract/v1','actors':[{'id':'actor_A','proxy_mode':'cylinder','character_name':'甲'}]}
        self.contract_sha=hashlib.sha256(json_payload_bytes(self.contract)).hexdigest()
        self.old={'source_timeline_sha256':self.timeline_sha,
                  'files':{'clean_video':{'path':'clean.mp4','sha256':self.clean_sha}},
                  'proxy_reference':{'clean_video_sha256':self.clean_sha,
                                     'mode_contract':{'path':'proxy-contract.json','sha256':self.contract_sha}}}

    def validate(self,contract=None):
        return validate_expanded_reuse(self.old,current_timeline_sha256=self.timeline_sha,
                                      current_clean_sha256=self.clean_sha,
                                      current_proxy_contract=self.contract if contract is None else contract)

    def test_same_timeline_clean_and_contract_pass(self):
        result=self.validate()
        self.assertTrue(result['passed'])
        self.assertEqual(result['mode_contract_file_sha256'],self.contract_sha)
        self.assertEqual(result['clean_video_sha256'],self.clean_sha)

    def test_same_timeline_different_clean_rejected(self):
        self.old['files']['clean_video']['sha256']='3'*64
        self.old['proxy_reference']['clean_video_sha256']='3'*64
        with self.assertRaisesRegex(ValueError,'clean video SHA256 differs'):
            self.validate()

    def test_same_clean_different_contract_rejected(self):
        changed=copy.deepcopy(self.contract)
        changed['actors'][0]['proxy_mode']='articulated_special'
        with self.assertRaisesRegex(ValueError,'mode contract file SHA256 differs'):
            self.validate(changed)

    def test_missing_contract_binding_rejected(self):
        del self.old['proxy_reference']['mode_contract']
        with self.assertRaisesRegex(ValueError,'mode contract file SHA256 differs'):
            self.validate()

    def test_source_binding_must_name_same_clean_hash(self):
        self.old['proxy_reference']['clean_video_sha256']='4'*64
        with self.assertRaisesRegex(ValueError,'not bound to the current clean video'):
            self.validate()

    def test_different_timeline_still_rejected(self):
        self.old['source_timeline_sha256']='5'*64
        with self.assertRaisesRegex(ValueError,'timeline differs'):
            self.validate()

    def test_serialized_file_hash_is_not_replaced_by_semantic_hash(self):
        compact=json.dumps(self.contract,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        self.old['proxy_reference']['mode_contract']['sha256']=hashlib.sha256(compact).hexdigest()
        with self.assertRaisesRegex(ValueError,'mode contract file SHA256 differs'):
            self.validate()

    def test_json_bytes_match_existing_pipeline_write_format(self):
        payload={'中文':'保留原文\r\n第二行','actors':[{'id':'actor_A','color':[.12,.43,.72]}]}
        with tempfile.TemporaryDirectory(prefix='previs_reuse_serialization_') as directory:
            root=Path(directory)
            previous=root/'previous-format.json'
            previous.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            current=root/'current-format.json'
            write(current,payload)
            self.assertEqual(json_payload_bytes(payload),previous.read_bytes())
            self.assertEqual(current.read_bytes(),previous.read_bytes())


if __name__=='__main__':unittest.main(verbosity=2)
