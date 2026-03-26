/**
 * Dashboard Controller v2.0
 * Enhanced with pause/resume, loading steps animation, export support
 */

function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function updateRecordingUI(state) {
    const statusEl = document.getElementById('recordingStatus');
    const startBtn = document.getElementById('btnStartRecording');
    const stopBtn = document.getElementById('btnStopRecording');
    const pauseBtn = document.getElementById('btnPauseRecording');
    const resumeBtn = document.getElementById('btnResumeRecording');
    const timerEl = document.getElementById('timer');
    const counterEl = document.getElementById('activityCount');
    const panel = document.getElementById('recordingPanel');
    const miniMap = document.getElementById('miniMap');

    switch (state) {
        case 'recording':
            statusEl.className = 'recording-status recording';
            statusEl.innerHTML = '<span class="rec-dot"></span> Recording...';
            startBtn.style.display = 'none';
            stopBtn.style.display = 'inline-flex';
            pauseBtn.style.display = 'inline-flex';
            resumeBtn.style.display = 'none';
            panel.style.display = 'block';
            if (miniMap) miniMap.style.display = 'block';
            break;
        case 'paused':
            statusEl.className = 'recording-status processing';
            statusEl.textContent = 'Paused';
            pauseBtn.style.display = 'none';
            resumeBtn.style.display = 'inline-flex';
            stopBtn.style.display = 'inline-flex';
            break;
        case 'processing':
            statusEl.className = 'recording-status processing';
            statusEl.textContent = 'Processing...';
            stopBtn.style.display = 'none';
            pauseBtn.style.display = 'none';
            resumeBtn.style.display = 'none';
            panel.style.display = 'none';
            break;
        case 'completed':
            statusEl.className = 'recording-status completed';
            statusEl.textContent = 'Analysis Complete';
            startBtn.style.display = 'inline-flex';
            stopBtn.style.display = 'none';
            pauseBtn.style.display = 'none';
            resumeBtn.style.display = 'none';
            panel.style.display = 'none';
            if (timerEl) timerEl.style.display = 'none';
            if (counterEl) counterEl.style.display = 'none';
            break;
        default:
            statusEl.className = 'recording-status idle';
            statusEl.textContent = 'Ready to Record';
            startBtn.style.display = 'inline-flex';
            stopBtn.style.display = 'none';
            pauseBtn.style.display = 'none';
            resumeBtn.style.display = 'none';
            panel.style.display = 'none';
            if (timerEl) timerEl.style.display = 'none';
            if (counterEl) counterEl.style.display = 'none';
    }
}

async function startRecording() {
    const title = document.getElementById('recordingTitle').value || 'Untitled Recording';
    try {
        updateRecordingUI('recording');
        await screenRecorder.startRecording(title);
        showToast('Recording started! Your screen is being captured.');
    } catch (err) {
        updateRecordingUI('idle');
        if (err.name === 'NotAllowedError') {
            showToast('Screen sharing was cancelled.', 'error');
        } else {
            showToast('Failed to start recording: ' + err.message, 'error');
        }
    }
}

function pauseRecording() {
    screenRecorder.pauseRecording();
    updateRecordingUI('paused');
    showToast('Recording paused. Click Resume to continue.');
}

function resumeRecording() {
    screenRecorder.resumeRecording();
    updateRecordingUI('recording');
    showToast('Recording resumed.');
}

async function stopRecording() {
    updateRecordingUI('processing');
    const overlay = document.getElementById('loadingOverlay');
    overlay.style.display = 'flex';

    // Animate loading steps
    const steps = ['step1', 'step2', 'step3', 'step4', 'step5', 'step6'];
    let stepIdx = 0;
    const stepInterval = setInterval(() => {
        if (stepIdx < steps.length) {
            const el = document.getElementById(steps[stepIdx]);
            if (el) el.style.color = 'var(--primary)';
            if (stepIdx > 0) {
                const prev = document.getElementById(steps[stepIdx - 1]);
                if (prev) prev.innerHTML = '&#10003; ' + prev.textContent;
            }
            stepIdx++;
        }
    }, 800);

    try {
        const result = await screenRecorder.stopRecording();
        clearInterval(stepInterval);
        overlay.style.display = 'none';

        if (result && result.status === 'completed') {
            updateRecordingUI('completed');
            showToast('Analysis complete! Opening report...');
            setTimeout(() => { window.location.href = `/report/${result.recording_id}`; }, 1000);
        } else {
            updateRecordingUI('idle');
            showToast('Recording stopped. Analysis may have failed.', 'error');
        }
    } catch (err) {
        clearInterval(stepInterval);
        overlay.style.display = 'none';
        updateRecordingUI('idle');
        showToast('Error: ' + err.message, 'error');
    }

    loadRecordings();
}

async function loadRecordings() {
    try {
        const resp = await fetch('/api/recordings');
        const recordings = await resp.json();

        document.getElementById('totalRecordings').textContent = recordings.length;
        document.getElementById('completedRecordings').textContent = recordings.filter(r => r.status === 'completed').length;

        const listEl = document.getElementById('recordingsList');

        if (!recordings.length) {
            listEl.innerHTML = '<li style="color:var(--gray-400); text-align:center; padding:20px;">No recordings yet. Start your first screen recording above.</li>';
            return;
        }

        let html = '';
        recordings.forEach(rec => {
            const duration = rec.duration_seconds ? `${Math.round(rec.duration_seconds)}s` : '--';
            const date = rec.started_at ? new Date(rec.started_at).toLocaleString() : '--';
            const statusClass = rec.status === 'completed' ? 'success' : rec.status === 'recording' ? 'danger' : 'warning';

            html += `
                <li>
                    <div class="rec-info">
                        <h4>${rec.title}</h4>
                        <span>${date} | Duration: ${duration}</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span class="badge badge-${statusClass}">${rec.status}</span>
                        ${rec.status === 'completed' ? `
                            <a href="/report/${rec.id}" class="btn btn-sm btn-outline">View Report</a>
                            <button class="btn btn-sm btn-outline" onclick="exportReport(${rec.id})" title="Export">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            </button>
                        ` : ''}
                    </div>
                </li>`;
        });
        listEl.innerHTML = html;
    } catch (err) {
        console.error('Failed to load recordings:', err);
    }
}

async function exportReport(recordingId) {
    try {
        const resp = await fetch(`/api/reports/${recordingId}/export`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `process_report_${recordingId}.html`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('Report exported successfully!');
    } catch (err) {
        // Fallback: open report in new tab for manual save
        window.open(`/report/${recordingId}`, '_blank');
        showToast('Opened report in new tab for export.');
    }
}

loadRecordings();
