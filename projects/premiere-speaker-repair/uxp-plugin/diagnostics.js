(function installTranscriptReviewDiagnostics() {
  try {
    const fs = require('fs');
    const inbox = '/LOCAL_BUILD/premiere-speaker-repair/work/uxp/ipc/inbox';
    const storageKey = 'transcriptReviewDiagnosticQueue';

    function makeRecord(value, context, details) {
      let message = String(value || '不明なエラー');
      let stack = '';
      if (value && typeof value === 'object') {
        message = value.message || message;
        stack = value.stack || '';
      }
      return {
        source: 'premiere-uxp-panel',
        level: 'error',
        context: context || 'unknown',
        message,
        stack,
        plugin_version: '0.3.0',
        host: typeof navigator !== 'undefined' ? navigator.userAgent : 'Premiere UXP',
        client_time: new Date().toISOString(),
        details: details || {},
      };
    }

    async function writeRecord(record) {
      const id = `log-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const request = {id, path: '/api/logs', payload: record, fire_and_forget: true};
      await fs.writeFile(`${inbox}/${id}.json`, JSON.stringify(request), {encoding: 'utf-8'});
    }

    function saveFallback(record) {
      try {
        const current = JSON.parse(localStorage.getItem(storageKey) || '[]');
        const queue = (Array.isArray(current) ? current : []).concat([record]).slice(-50);
        localStorage.setItem(storageKey, JSON.stringify(queue));
      } catch (_) {}
    }

    function report(value, context = 'unknown', details = {}) {
      const record = makeRecord(value, context, details);
      writeRecord(record).catch(() => saveFallback(record));
    }

    async function flush() {
      let queue = [];
      try { queue = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch (_) {}
      if (!Array.isArray(queue) || !queue.length) return;
      const remaining = [];
      for (const record of queue) {
        try { await writeRecord(record); } catch (_) { remaining.push(record); }
      }
      try { localStorage.setItem(storageKey, JSON.stringify(remaining.slice(-50))); } catch (_) {}
    }

    window.uxpDiagnostics = {report, flush};
    try {
      window.addEventListener('error', event => report(event.error || event.message, 'window.error'));
      window.addEventListener('unhandledrejection', event => report(event.reason, 'unhandledrejection'));
    } catch (_) {}
    setTimeout(flush, 500);
  } catch (error) {
    try { console.error('[TranscriptReview diagnostics]', error); } catch (_) {}
  }
})();
