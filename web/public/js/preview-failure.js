// Formal-page only: the signed bootstrap may finish after the client has loaded.
// Its tab-local marker contains no user data; never infer a failure from the absence of a preview cookie.
import { SEAT } from './seats.js';
import { t } from './t.js';
import { notify } from './ui.js';
export function placeStrip(banner) {
  const chat = document.getElementById('chat-view'), stage = document.getElementById('stage');
  if (chat && stage?.parentElement === chat && document.body.dataset.view === 'chat') chat.insertBefore(banner, stage);
  else document.body.appendChild(banner);
}
export function watchPreviewFailure() {
  if (!SEAT || document.querySelector('meta[name="agentj-web-preview"]')) return;
  const marker = 'aj.preview.failed.' + SEAT;
  const showFailure = () => {
    try {
      if (sessionStorage.getItem(marker) !== '1') return false;
      sessionStorage.removeItem(marker);
      const banner = notify({kind:'warn', text:t('preview.failedFormal'), actions:[{label:t('preview.dismiss'), fn:()=>{}}], ms:10000});
      // This failure belongs to the page, not to a reply. 0.30.7 (S3, chairman 11-11): in the chat it is its own strip between
      // the header and the reply card — the card moves down instead of losing its title under it; elsewhere it is the fixed
      // Noir strip at the top. A confirmation card on top (body[data-ontop]) stays above it either way (app.css).
      if (banner) { banner.dataset.previewFailure = '1'; placeStrip(banner); }
      return true;
    } catch { return false; /* private browsing can block storage */ }
  };
  if (!showFailure()) {
    let ticks = 0;
    const watcher = setInterval(() => { if (showFailure() || ++ticks >= 120) clearInterval(watcher); }, 250);
  }
}
