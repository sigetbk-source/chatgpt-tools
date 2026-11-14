const { entrypoints } = require('uxp');

entrypoints.setup({
  panels: {
    transcriptReviewPanel: {
      create() {},
      show() {
        setTimeout(() => {
          if (typeof window.onTranscriptPanelShow === 'function') {
            Promise.resolve(window.onTranscriptPanelShow()).catch(() => {});
          }
        }, 0);
      },
    },
  },
  commands: {
    applyPendingTranscript() {
      if (typeof window.runApplyPendingTranscriptCommand !== 'function') {
        alert('文字起こし修正パネルを一度開いてから、もう一度実行してください。');
        return;
      }
      Promise.resolve(window.runApplyPendingTranscriptCommand()).catch(error => {
        try { window.uxpDiagnostics?.report(error, 'apply-pending-command'); } catch (_) {}
        alert(`反映処理を開始できませんでした: ${error?.message || error}`);
      });
    },
    applyNarrationTranscripts() {
      if (typeof window.runNarrationTranscriptCommand !== 'function') {
        alert('文字起こし修正パネルを一度開いてから、もう一度実行してください。');
        return;
      }
      Promise.resolve(window.runNarrationTranscriptCommand()).catch(error => {
        try { window.uxpDiagnostics?.report(error, 'apply-narration-transcripts-command'); } catch (_) {}
        alert(`ナレーション文字起こしを登録できませんでした: ${error?.message || error}`);
      });
    },
  },
});
