import io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from multilingual_review import Workspace, Conflict, align_provider_text, candidate_words, retry_fingerprint

def fixture():
    return {'schema':'bk-master-transcript/v0.1','source_id':'sample','language':'ja-jp',
        'speakers':[{'key':'a','name':'A'}], 'utterances':[{'speaker':'a','words':[
            {'text':'Hallo','start':1.0,'end':1.5},{'text':' Welt','start':1.5,'end':2.0}]}]}

class MultilingualReviewTest(unittest.TestCase):
    def test_split_uses_exact_caret_boundary_and_keeps_word_times(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work')
            row=work.project()['items'][0]
            self.assertEqual(row['word_boundaries'],[5])
            payload={'revision':0,'segment_index':0,'current_text':'Hallo Welt'}
            with self.assertRaisesRegex(ValueError,'exact word boundary'):
                work.mutate('/api/split',{**payload,'caret':4})
            self.assertEqual(work.state['revision'],0)
            work.mutate('/api/split',{**payload,'caret':5})
            rows=work.project()['items']
            self.assertEqual([row['text'] for row in rows],['Hallo','Welt'])
            self.assertEqual([(row['start_seconds'],row['end_seconds']) for row in rows],[(1.0,1.5),(1.5,2.0)])
            self.assertEqual([row['speaker_name'] for row in rows],['A','A'])
            work.close()

    def test_legacy_leading_space_candidate_can_be_adopted_only_with_matching_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; value=fixture()
            value['utterances'][0]['words'][0]['text']=' Hallo'
            master.write_text(json.dumps(value),encoding='utf-8'); work=Workspace(master,root/'work')
            utterance=work.state['master']['utterances'][0]
            candidate={'id':'legacy','status':'candidate','text':'Guten Tag','base_text':' Hallo Welt',
                       'fingerprint':retry_fingerprint(utterance,work.state['source'],' Hallo Welt'),
                       'words':[{'text':'Guten','start':1.0,'end':1.5},{'text':' Tag','start':1.5,'end':2.0}]}
            utterance['retry_candidates']=[candidate,{'id':'untimed','status':'candidate','text':'x','words':[]}]
            self.assertIsNone(work.project()['items'][0]['retry_candidates'][0]['adoption_error'])
            self.assertIn('単語時刻がない',work.project()['items'][0]['retry_candidates'][1]['adoption_error'])
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'de-de'})
            self.assertIn('変わりました',work.project()['items'][0]['retry_candidates'][0]['adoption_error'])
            with self.assertRaises(Conflict):
                work.mutate('/api/retry/adopt',{'revision':1,'segment_index':0,'candidate_id':'legacy','current_text':'Hallo Welt'})
            work.mutate('/api/undo',{'revision':1})
            self.assertIsNone(work.project()['items'][0]['retry_candidates'][0]['adoption_error'])
            work.mutate('/api/retry/adopt',{'revision':2,'segment_index':0,'candidate_id':'legacy','current_text':'Hallo Welt'})
            self.assertEqual(work.project()['items'][0]['text'],'Guten Tag')
            work.close()

    def test_manual_alignment_requires_exact_text_and_confirmed_monotonic_times(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; value=fixture()
            value['utterances'].append({'speaker':'a','words':[{'text':' 次','start':3.0,'end':3.5}]})
            master.write_text(json.dumps(value),encoding='utf-8'); work=Workspace(master,root/'work')
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'de-de'})
            work.mutate('/api/translation',{'revision':1,'segment_index':0,'text':'日本語訳'})
            work.mutate('/api/text',{'revision':2,'segment_index':0,'text':'Japanische Fernsehsendung.'})
            row=work.project()['items'][0]; fingerprint=row['retry_fingerprint']
            self.assertEqual(row['alignment_status'],'unresolved')
            self.assertEqual(len(row['timing_words']),2)
            other_before=json.loads(json.dumps(work.state['master']['utterances'][1]))
            payload={'revision':3,'segment_index':0,'current_text':row['text'],'expected_fingerprint':fingerprint,
                     'words':[{'text':'Japanische','start':1.0,'end':1.4},
                              {'text':' Fernsehsendung.','start':1.4,'end':2.0}]}
            for changed,pattern in [({'current_text':'old'},'変わりました'),
                                    ({'expected_fingerprint':'old'},'変わりました'),
                                    ({'words':[{'text':'Japanische','start':1.0,'end':1.7},
                                               {'text':' Fernsehsendung.','start':1.6,'end':2.0}]},'単語時刻'),
                                    ({'words':[{'text':'Japanische','start':1.0,'end':1.4},
                                               {'text':' falsch','start':1.4,'end':2.0}]},'一致しません')]:
                with self.assertRaisesRegex((Conflict,ValueError),pattern):
                    work.mutate('/api/align/manual',{**payload,**changed})
                self.assertEqual(work.state['revision'],3)
            work.mutate('/api/align/manual',payload)
            aligned=work.project()['items'][0]
            self.assertEqual((aligned['text'],aligned['alignment_status']),('Japanische Fernsehsendung.','word-timed'))
            self.assertEqual(work.state['master']['utterances'][0]['alignment_source'],'manual-confirmed')
            self.assertEqual(work.state['master']['utterances'][1],other_before)
            work.mutate('/api/undo',{'revision':4})
            self.assertEqual(work.project()['items'][0]['alignment_status'],'unresolved')
            work.close()

    def test_translation_staleness_candidate_and_export_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8'); before=master.read_bytes()
            aai=root/'aai.json'; aai.write_text(json.dumps({'words':[{'text':'Hallo','start':1000,'end':1500},{'text':'Welt','start':1500,'end':2000}]}))
            work=Workspace(master,root/'work',aai_response=aai)
            self.assertEqual(work.project()['items'][0]['language'],'??-??')
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'de-de'})
            work.mutate('/api/translation',{'revision':1,'segment_index':0,'text':'こんにちは世界'})
            work.mutate('/api/retry/saved',{'revision':2,'segment_index':0,'language':'de-de'})
            row=work.project()['items'][0]; self.assertEqual(row['text'],'Hallo Welt'); self.assertEqual(len(row['retry_candidates']),1)
            complete=work.export(False); self.assertIn('<i>Hallo Welt</i>\n\u200b\nこんにちは世界',Path(complete['srt_path']).read_text())
            self.assertIsNotNone(complete['json_path'])
            work.mutate('/api/text',{'revision':3,'segment_index':0,'text':'Guten Tag'})
            self.assertEqual(work.project()['items'][0]['translation']['status'],'stale')
            with self.assertRaisesRegex(ValueError,'完全書き出しを停止'): work.export(False)
            draft=work.export(True); self.assertIn('[日本語訳 未完了]',Path(draft['srt_path']).read_text()); self.assertIsNone(draft['json_path'])
            work.mutate('/api/translation',{'revision':4,'segment_index':0,'text':'こんにちは'})
            srt_only=work.export(False); self.assertIsNone(srt_only['json_path']); self.assertIn('<i>Guten Tag</i>\n\u200b\nこんにちは',Path(srt_only['srt_path']).read_text())
            self.assertEqual(master.read_bytes(),before); work.close()

    def test_rename_keeps_speaker_key_uuid_and_supports_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work'); key=work.state['master']['speakers'][0]['key']; speaker_id=work.state['speaker_ids'][key]
            work.mutate('/api/speaker/rename',{'revision':0,'segment_index':0,'name':'通訳'})
            self.assertEqual(work.project()['items'][0]['speaker_name'],'通訳')
            self.assertEqual(work.state['master']['speakers'][0]['key'],key); self.assertEqual(work.state['speaker_ids'][key],speaker_id)
            with self.assertRaisesRegex(ValueError,'unique'):
                work.mutate('/api/speaker/rename',{'revision':1,'segment_index':0,'name':'通訳'})
            work.mutate('/api/undo',{'revision':1}); self.assertEqual(work.project()['items'][0]['speaker_name'],'A'); work.close()

    def test_atomic_speaker_manager_adds_and_optionally_assigns(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work'); old_key=work.state['master']['speakers'][0]['key']; old_id=work.state['speaker_ids'][old_key]
            work.mutate('/api/speakers/manage',{'revision':0,'existing':[{'key':old_key,'name':'司会'}],
                'new_names':['ゲスト'],'segment_index':0,'assign_new_index':0})
            self.assertEqual([x['name'] for x in work.state['master']['speakers']],['司会','ゲスト'])
            self.assertEqual(work.state['speaker_ids'][old_key],old_id)
            self.assertEqual(work.project()['items'][0]['speaker_name'],'ゲスト')
            work.mutate('/api/undo',{'revision':1}); self.assertEqual(work.project()['speaker_options'],['A']); work.close()

    def test_multilingual_merge_preserves_metadata_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); value=fixture(); value['utterances']=[
                {'speaker':'a','language':'de-de','language_source':'manual','text_override':'Guten Tag',
                 'alignment_status':'unresolved','translation':{'text':'こんにちは','status':'ready','provider':'manual'},
                 'retry_candidates':[{'id':'left','status':'candidate','base_text':'Guten Tag'}],
                 'words':[{'text':'Guten','start':1.0,'end':1.4},{'text':' Tag','start':1.4,'end':2.0}]},
                {'speaker':'a','language':'ja-jp','language_source':'manual','translation':{'text':'右訳','status':'ready','provider':'manual'},
                 'retry_candidates':[{'id':'right','status':'adopted'}], 'custom_note':'keep-right',
                 'words':[{'text':' です','start':2.1,'end':2.5}]},
                {'speaker':'a','language':'ja-jp','language_source':'manual','translation':{'text':'次の訳','status':'ready','provider':'manual'},
                 'words':[{'text':'次','start':3.0,'end':3.5}]}]
            master=root/'input.json'; master.write_text(json.dumps(value),encoding='utf-8'); work=Workspace(master,root/'work')
            third_before=json.loads(json.dumps(work.state['master']['utterances'][2]))
            work.mutate('/api/merge',{'revision':0,'segment_index':0,'current_text':'Guten Tag','next_text':'です'})
            merged=work.state['master']['utterances'][0]
            self.assertEqual(work.project()['items'][0]['text'],'Guten Tagです'); self.assertEqual(merged['language'],'??-??')
            self.assertEqual(merged['translation']['text'],'こんにちは\n右訳'); self.assertEqual(merged['translation']['status'],'stale')
            self.assertEqual([x['status'] for x in merged['retry_candidates']],['stale-after-merge','adopted'])
            self.assertEqual(merged['merge_provenance'][0]['right']['custom_note'],'keep-right')
            self.assertEqual(work.state['master']['utterances'][1],third_before)
            with self.assertRaisesRegex(ValueError,'次の発言がありません'):
                work.mutate('/api/merge',{'revision':1,'segment_index':1,'current_text':'次','next_text':''})
            work.mutate('/api/undo',{'revision':1}); self.assertEqual(len(work.state['master']['utterances']),3); work.close()

    def test_inferred_language_batch_is_atomic_protected_and_undoable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); value=fixture(); value['utterances']=[
                {'speaker':'a','words':[{'text':'境界','start':165.55,'end':166.0}]},
                {'speaker':'a','words':[{'text':'Hallo','start':166.0,'end':166.5}]},
                {'speaker':'a','language':'ja-jp','language_source':'manual','words':[{'text':'手動','start':167.0,'end':167.5}]}]
            master=root/'input.json'; master.write_text(json.dumps(value),encoding='utf-8'); work=Workspace(master,root/'work')
            before=json.loads(json.dumps(work.state['master']))
            with self.assertRaisesRegex(Conflict,'stale revision'):
                work.mutate('/api/language/infer-apply',{'revision':99,'cutoff_seconds':165.55,'assignments':[
                    {'segment_index':1,'language':'de-de','reason':'古い画面','confidence':'high'}]})
            work.mutate('/api/language/infer-apply',{'revision':0,'cutoff_seconds':165.55,'assignments':[
                {'segment_index':1,'language':'de-de','reason':'ドイツ語の語句','confidence':'high'}]})
            inferred=work.state['master']['utterances'][1]
            self.assertEqual((inferred['language'],inferred['language_source']),('de-de','inferred'))
            self.assertEqual(inferred['language_inference']['provider'],'text-review'); self.assertTrue(inferred['language_inference']['needs_review'])
            self.assertEqual(work.state['master']['utterances'][0],before['utterances'][0])
            work.mutate('/api/undo',{'revision':1}); self.assertEqual(work.state['master'],before)
            for assignment,error in [
                ({'segment_index':0,'language':'de-de','reason':'境界','confidence':.8},'after cutoff'),
                ({'segment_index':2,'language':'de-de','reason':'上書き','confidence':.8},'manual language')]:
                with self.assertRaisesRegex(ValueError,error):
                    work.mutate('/api/language/infer-apply',{'revision':2,'cutoff_seconds':165.55,'assignments':[assignment]})
                self.assertEqual(work.state['master'],before)
            work.close()

    def test_retry_adopts_provider_words_inside_preserved_envelope_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            aai=root/'aai.json'; aai.write_text(json.dumps({'speech_model_used':'universal-3-5-pro','words':[
                {'text':'Guten','start':1100,'end':1400,'confidence':.9},{'text':'Tag','start':1500,'end':1900,'confidence':.8}]}))
            work=Workspace(master,root/'work',aai_response=aai); before=work.state['master']['utterances'][0]
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'de-de'})
            work.mutate('/api/retry/saved',{'revision':1,'segment_index':0,'language':'de-de'})
            candidate=work.state['master']['utterances'][0]['retry_candidates'][0]
            speaker=work.state['master']['utterances'][0]['speaker']; envelope=dict(work.state['master']['utterances'][0]['review_envelope'])
            work.mutate('/api/retry/adopt',{'revision':2,'segment_index':0,'candidate_id':candidate['id'],'current_text':'Hallo Welt'})
            adopted=work.state['master']['utterances'][0]
            self.assertEqual(adopted['speaker'],speaker); self.assertEqual(adopted['language'],'de-de'); self.assertEqual(adopted['review_envelope'],envelope)
            self.assertEqual([(w['text'],w['start'],w['end']) for w in adopted['words']],[('Guten',1.1,1.4),(' Tag',1.5,1.9)])
            self.assertEqual(work.project()['items'][0]['text'],'Guten Tag')
            work.mutate('/api/undo',{'revision':3}); self.assertEqual(work.state['master']['utterances'][0]['words'],before['words'])
            work.mutate('/api/language',{'revision':4,'segment_index':0,'language':'en-us'})
            with self.assertRaisesRegex(Conflict,'候補作成後に変わりました'):
                work.mutate('/api/retry/adopt',{'revision':5,'segment_index':0,'candidate_id':candidate['id'],'current_text':'Hallo Welt'})
            work.close()

    def test_retry_rejects_bad_times_and_stale_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            bad=root/'bad.json'; bad.write_text(json.dumps({'words':[{'text':'bad','start':500,'end':2500}]}))
            work=Workspace(master,root/'work',aai_response=bad)
            with self.assertRaisesRegex(ValueError,'単語時刻がありません'):
                work.mutate('/api/retry/saved',{'revision':0,'segment_index':0,'language':'de-de'})
            self.assertEqual(work.state['revision'],0); work.close()

    def test_provider_word_validation_does_not_fabricate_lexical_timing(self):
        with self.assertRaisesRegex(ValueError,'単語時刻が不正'):
            candidate_words([{'text':'bad','start':float('nan'),'end':1.0}],0,2)
        words=candidate_words([{'text':'Hello','start':0.0,'end':.5},{'text':'world','start':.6,'end':1.0}],0,2)
        aligned,text=align_provider_text(words,'Hello brave world')
        self.assertEqual([x['text'] for x in aligned],['Hello','world']); self.assertEqual(text,'Hello brave world')
        punctuated,text=align_provider_text(words,'Hello, world!')
        self.assertEqual(''.join(x['text'] for x in punctuated),'Hello, world!')

    def test_runtime_aai_config_is_not_persisted_or_added_to_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work'); state_before=work.path.read_bytes()
            result=work.mutate('/api/config/assemblyai',{'revision':0,'key':'secret-test-value','model':'universal-3-5-pro','ffmpeg':'/bin/echo'})
            self.assertEqual(result,{'ok':True,'configured':True,'revision':0}); self.assertTrue(work.project()['providers']['assemblyai_live'])
            self.assertEqual(work.path.read_bytes(),state_before); self.assertNotIn(b'secret-test-value',work.path.read_bytes()); work.close()

    def test_japanese_display_removes_only_unneeded_spaces_and_auto_adopts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); value=fixture(); value['language']='ja-jp'
            value['utterances'][0]['words']=[{'text':'か？','start':1.0,'end':1.2},{'text':' 六','start':1.3,'end':1.5},{'text':' 種','start':1.6,'end':1.8},{'text':' 類','start':1.9,'end':2.0}]
            master=root/'input.json'; master.write_text(json.dumps(value),encoding='utf-8')
            aai=root/'aai.json'; aai.write_text(json.dumps({'words':[{'text':'日本','start':1000,'end':1400},{'text':'語','start':1500,'end':1900}]}))
            work=Workspace(master,root/'work',aai_response=aai); self.assertEqual(work.project()['items'][0]['text'],'か？六種類')
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'ja-jp'})
            work.mutate('/api/retry/saved',{'revision':1,'segment_index':0,'language':'ja-jp','auto_adopt':True})
            row=work.project()['items'][0]; self.assertEqual(row['text'],'日本語'); self.assertEqual(row['alignment_status'],'word-timed')
            self.assertEqual(work.state['master']['utterances'][0]['retry_candidates'][0]['status'],'adopted'); work.close()

    def test_batch_live_guard_calls_provider_only_for_unchanged_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8'); work=Workspace(master,root/'work')
            fingerprint=work.project()['items'][0]['retry_fingerprint']
            def candidate(utterance,payload):
                utterance['retry_candidates'].append({'id':'mock','status':'candidate','text':'mock','words':deepcopy(utterance['words']),
                    'base_text':'Hallo Welt','fingerprint':fingerprint})
            from copy import deepcopy
            with patch.object(work,'_live_retry',side_effect=candidate) as provider:
                work.mutate('/api/retry/live',{'revision':0,'segment_index':0,'language':'de-de','expected_fingerprint':fingerprint})
                with self.assertRaisesRegex(Conflict,'送信しませんでした'):
                    work.mutate('/api/retry/live',{'revision':1,'segment_index':0,'language':'de-de','expected_fingerprint':'stale'})
                self.assertEqual(provider.call_count,1)
            work.close()

    def test_context_translation_preserves_source_and_manual_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); value=fixture(); value['speakers'].append({'key':'b','name':'通訳'})
            value['utterances']=[
                {'speaker':'b','language':'ja-jp','words':[{'text':'これは赤い車です','start':0,'end':.8}]},
                {'speaker':'a','language':'de-de','words':[{'text':'Das rote Auto','start':1,'end':2}]},
                {'speaker':'b','language':'ja-jp','words':[{'text':'赤い車ですね','start':2.2,'end':3}]},
                {'speaker':'a','language':'en-us','words':[{'text':'Yes','start':4,'end':4.5}]}]
            master=root/'input.json'; master.write_text(json.dumps(value),encoding='utf-8')
            work=Workspace(master,root/'work'); work.translation_config={'key':'test-key','model':'gpt-4.1-mini'}
            before=json.loads(json.dumps(work.state['master']['utterances']))
            fingerprint=work.project()['items'][1]['translation_fingerprint']; requests=[]
            def respond(request,timeout):
                requests.append(json.loads(request.data))
                return io.BytesIO(json.dumps({'output':[{'content':[{'type':'output_text','text':'{"text":"赤い車"}'}]}]}).encode())
            with patch('urllib.request.urlopen',side_effect=respond):
                with self.assertRaisesRegex(Conflict,'送信しませんでした'):
                    work.mutate('/api/translation/generate',{'revision':0,'segment_index':1,'expected_fingerprint':'changed'})
                work.mutate('/api/translation/generate',{'revision':0,'segment_index':1,'expected_fingerprint':fingerprint})
                with self.assertRaisesRegex(ValueError,'既存の訳'):
                    work.mutate('/api/translation/generate',{'revision':1,'segment_index':1})
            self.assertEqual(len(requests),1)
            body=requests[0]; self.assertFalse(body['store']); self.assertEqual(body['model'],'gpt-4.1-mini')
            self.assertEqual([x['position'] for x in json.loads(body['input'])['dialogue']],[-1,0,1,2])
            self.assertEqual(json.loads(body['input'])['dialogue'][0]['speaker'],'通訳')
            self.assertIn('position 0',body['instructions']); self.assertIn('原文要確認',body['instructions'])
            self.assertEqual(work.project()['items'][1]['translation']['text'],'赤い車')
            self.assertEqual([u['words'] for u in work.state['master']['utterances']],[u['words'] for u in before])
            self.assertEqual([u['speaker'] for u in work.state['master']['utterances']],[u['speaker'] for u in before])
            work.mutate('/api/translation',{'revision':1,'segment_index':3,'text':'手入力'})
            with patch('urllib.request.urlopen') as provider:
                with self.assertRaisesRegex(ValueError,'既存の訳'):
                    work.mutate('/api/translation/generate',{'revision':2,'segment_index':3})
                provider.assert_not_called()
            work.close()

    def test_unknown_language_and_empty_result_do_not_mutate_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work'); work.translation_config={'key':'test-key','model':'gpt-4.1-mini'}
            with patch('urllib.request.urlopen') as provider:
                with self.assertRaisesRegex(ValueError,'未指定'):
                    work.mutate('/api/translation/generate',{'revision':0,'segment_index':0})
                provider.assert_not_called()
            work.mutate('/api/language',{'revision':0,'segment_index':0,'language':'de-de'})
            before=work.path.read_bytes()
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"output":[{"content":[{"type":"output_text","text":"{\\"text\\":\\"\\"}"}]}]}')):
                with self.assertRaisesRegex(ValueError,'空'):
                    work.mutate('/api/translation/generate',{'revision':1,'segment_index':0})
            self.assertEqual(work.path.read_bytes(),before); work.close()

    def test_runtime_translation_setup_stays_out_of_saved_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); master=root/'input.json'; master.write_text(json.dumps(fixture()),encoding='utf-8')
            work=Workspace(master,root/'work'); saved=work.path.read_bytes()
            work.mutate('/api/config/translation',{'revision':0,'key':'runtime-only-key','model':'gpt-4.1-mini'})
            self.assertTrue(work.project()['providers']['translation'])
            self.assertEqual(work.state['revision'],0)
            self.assertEqual(work.path.read_bytes(),saved)
            self.assertNotIn(b'runtime-only-key',work.path.read_bytes())
            work.close()

if __name__=='__main__': unittest.main()
