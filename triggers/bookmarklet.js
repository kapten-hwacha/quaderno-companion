/**
 * Quaderno Companion - 1-Click Browser Bookmarklet
 * 
 * Usage:
 * Create a new browser bookmark with the URL set to:
 * javascript:(function(){fetch('http://localhost:5000/api/agent/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:window.location.href,title:document.title})}).then(r=>r.json()).then(d=>alert('Pushed to Quaderno: '+d.details.title)).catch(e=>alert('Error pushing to Quaderno: '+e));})();
 */

(function () {
    const endpoint = 'http://localhost:5000/api/agent/push';
    const payload = {
        url: window.location.href,
        title: document.title,
        summarize: false
    };

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    })
    .then(data => {
        console.log('Quaderno Push Success:', data);
        alert('✓ Pushed to Quaderno: ' + (data.details ? data.details.title : document.title));
    })
    .catch(err => {
        console.error('Quaderno Push Error:', err);
        alert('✗ Failed to push to Quaderno daemon on localhost:5000. Is `quadctl serve` running?');
    });
})();
