// Laibrary PWA Chat Application with Message Queueing

class LaibraryChat {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;
        this.currentProject = null;
        this.pendingCount = 0;
        this.lastSeenMessageId = 0;
        this.pendingMessages = new Map(); // message_id -> {element, content}
        this.processedMessageIds = new Set(); // dedup: IDs already displayed
        this.pollInterval = null;
        this.usePolling = false;

        this.elements = {
            messages: document.getElementById('messages'),
            form: document.getElementById('chat-form'),
            input: document.getElementById('message-input'),
            sendBtn: document.getElementById('send-btn'),
            projectSelect: document.getElementById('project-select'),
            connectionStatus: document.getElementById('connection-status'),
            btnViewProjects: document.getElementById('btn-view-projects'),
            btnCreateProject: document.getElementById('btn-create-project'),
            slashAutocomplete: document.getElementById('slash-autocomplete')
        };

        this.slashActiveIndex = -1;
        this.slashCommands = [
            { cmd: '/list', hint: 'Show available projects' },
            { cmd: '/projects', hint: 'Show available projects' },
            { cmd: '/use', hint: 'Select a project', suffix: ' ' },
            { cmd: '/read', hint: 'Print project document', suffix: ' ' },
            { cmd: '/clear', hint: 'Clear chat history' },
        ];
        this.projectNames = [];

        this.init();
    }

    init() {
        this.elements.form.addEventListener('submit', (e) => this.handleSubmit(e));
        this.elements.projectSelect.addEventListener('change', (e) => this.handleProjectChange(e));

        // Slash autocomplete
        this.elements.input.addEventListener('input', () => this.onInputChange());
        this.elements.input.addEventListener('keydown', (e) => this.onInputKeydown(e));
        // Close autocomplete when tapping elsewhere
        document.addEventListener('click', (e) => {
            if (!this.elements.form.contains(e.target)) {
                this.hideSlashAutocomplete();
            }
        });

        // Welcome screen buttons
        if (this.elements.btnViewProjects) {
            this.elements.btnViewProjects.addEventListener('click', () => this.openProjectSelector());
        }
        if (this.elements.btnCreateProject) {
            this.elements.btnCreateProject.addEventListener('click', () => this.promptNewProject());
        }

        this.connectWebSocket();
        this.fetchProjects();
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.reconnectAttempts = 0;
            this.reconnectDelay = 1000;
            this.setConnectionStatus('connected');
            this.usePolling = false;
            this.stopPolling();
        };

        this.ws.onclose = () => {
            this.setConnectionStatus('disconnected');
            this.scheduleReconnect();
            // Start polling as fallback if we have pending messages
            if (this.pendingMessages.size > 0) {
                this.startPolling();
            }
        };

        this.ws.onerror = () => {
            this.setConnectionStatus('error');
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleServerMessage(data);
        };
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.setConnectionStatus('failed');
            this.usePolling = true;
            this.startPolling();
            return;
        }

        this.reconnectAttempts++;
        const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts), 30000);

        setTimeout(() => {
            this.setConnectionStatus('reconnecting');
            this.connectWebSocket();
        }, delay);
    }

    startPolling() {
        if (this.pollInterval) return;
        this.pollInterval = setInterval(() => this.poll(), 1000);
    }

    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    }

    async poll() {
        try {
            const response = await fetch(`/api/poll?since=${this.lastSeenMessageId}`);
            const data = await response.json();

            if (data.current_project) {
                this.updateProjectBadge(data.current_project);
            }
            this.pendingCount = data.pending_count || 0;
            this.updatePendingIndicator();

            for (const update of data.updates || []) {
                this.handleServerMessage(update);
            }
        } catch (e) {
            // Ignore polling errors
        }
    }

    setConnectionStatus(status) {
        const el = this.elements.connectionStatus;
        el.className = `status ${status}`;

        switch (status) {
            case 'connected':
                el.textContent = '';
                break;
            case 'disconnected':
            case 'reconnecting':
                el.textContent = 'Reconnecting...';
                break;
            case 'error':
            case 'failed':
                el.textContent = 'Offline';
                break;
        }
    }

    handleServerMessage(data) {
        switch (data.type) {
            case 'status':
                // Initial status on connect
                if (data.current_project) {
                    this.updateProjectBadge(data.current_project);
                }
                this.pendingCount = data.pending_count || 0;
                this.updatePendingIndicator();
                break;

            case 'immediate':
                // Immediate response (for /list, /use, etc.)
                this.setLoading(false);
                this.addMessage(data.response, 'assistant');
                if (data.current_project) {
                    this.updateProjectBadge(data.current_project);
                }
                break;

            case 'queued':
                // Message was queued, show pending indicator
                this.setLoading(false);
                this.pendingCount = data.pending_count || 0;
                this.updatePendingIndicator();
                this.showPendingMessage(data.message_id);
                break;

            case 'completed':
                // Message completed - skip if already processed
                if (this.processedMessageIds.has(data.message_id)) break;
                this.processedMessageIds.add(data.message_id);
                this.lastSeenMessageId = Math.max(this.lastSeenMessageId, data.message_id);
                this.resolvePendingMessage(data.message_id, data.response, false);
                if (data.current_project) {
                    this.updateProjectBadge(data.current_project);
                }
                this.pendingCount = Math.max(0, this.pendingCount - 1);
                this.updatePendingIndicator();
                break;

            case 'failed':
                // Message failed - skip if already processed
                if (this.processedMessageIds.has(data.message_id)) break;
                this.processedMessageIds.add(data.message_id);
                this.lastSeenMessageId = Math.max(this.lastSeenMessageId, data.message_id);
                this.resolvePendingMessage(data.message_id, data.error, true);
                this.pendingCount = Math.max(0, this.pendingCount - 1);
                this.updatePendingIndicator();
                break;

            case 'cleared':
                // Chat history cleared
                this.setLoading(false);
                this.clearMessages();
                break;

            case 'error':
                // General error
                this.setLoading(false);
                this.addMessage(data.error, 'error');
                break;
        }
    }

    clearMessages() {
        // Remove all message elements from the DOM
        const messages = this.elements.messages.querySelectorAll('.message');
        messages.forEach(el => el.remove());
        // Reset tracking state
        this.pendingMessages.clear();
        this.processedMessageIds.clear();
        this.lastSeenMessageId = 0;
    }

    async handleSubmit(e) {
        e.preventDefault();

        const message = this.elements.input.value.trim();
        if (!message) return;

        this.elements.input.value = '';
        this.hideSlashAutocomplete();
        this.addMessage(message, 'user');
        this.setLoading(true);

        // Try WebSocket first, fall back to HTTP
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ message }));
        } else {
            await this.sendHttp(message);
        }
    }

    async sendHttp(message) {
        try {
            const response = await fetch('/api/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });

            const data = await response.json();
            this.handleServerMessage(data);

            // Start polling if message was queued
            if (data.type === 'queued') {
                this.startPolling();
            }
        } catch (e) {
            this.handleServerMessage({ type: 'error', error: 'Failed to send message. Are you online?' });
        }
    }

    showPendingMessage(messageId) {
        // Create a pending message element
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant pending';
        messageEl.innerHTML = '<span class="pending-indicator">Processing...</span>';
        this.elements.messages.appendChild(messageEl);
        this.scrollToBottom();

        this.pendingMessages.set(messageId, { element: messageEl });
    }

    resolvePendingMessage(messageId, content, isError) {
        const pending = this.pendingMessages.get(messageId);

        if (pending) {
            // Update existing pending element
            pending.element.classList.remove('pending');
            if (isError) {
                pending.element.classList.remove('assistant');
                pending.element.classList.add('error');
            }
            pending.element.innerHTML = this.renderMarkdown(content);
            this.pendingMessages.delete(messageId);
        } else {
            // No pending element found, just add new message
            this.addMessage(content, isError ? 'error' : 'assistant');
        }
        this.scrollToBottom();
    }

    addMessage(content, role) {
        // Remove welcome message if present
        const welcome = this.elements.messages.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }

        const messageEl = document.createElement('div');
        messageEl.className = `message ${role}`;
        messageEl.innerHTML = this.renderMarkdown(content);

        this.elements.messages.appendChild(messageEl);
        this.scrollToBottom();
    }

    renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            return marked.parse(text, { breaks: true, gfm: true });
        }
        // Fallback: escape HTML and convert newlines
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    updatePendingIndicator() {
        // Update pending count display (now handled in dropdown area)
        // Could add visual indicator to dropdown if needed
    }

    setLoading(loading) {
        this.elements.sendBtn.disabled = loading;
        if (loading) {
            this.elements.sendBtn.classList.add('loading');
        } else {
            this.elements.sendBtn.classList.remove('loading');
        }
    }

    // --- Slash autocomplete ---

    getSlashSuggestions(query) {
        // query is the text after "/" (e.g. "" for just "/", "li" for "/li", "use pro" for "/use pro")
        const lower = query.toLowerCase();
        const suggestions = [];

        // Match built-in commands
        for (const item of this.slashCommands) {
            const cmdName = item.cmd.slice(1); // remove leading "/"
            if (cmdName.startsWith(lower)) {
                suggestions.push(item);
            }
        }

        return suggestions;
    }

    onInputChange() {
        const value = this.elements.input.value;

        if (value.startsWith('/') && !value.includes(' ')) {
            const query = value.slice(1);
            const suggestions = this.getSlashSuggestions(query);
            if (suggestions.length > 0) {
                this.showSlashAutocomplete(suggestions);
                return;
            }
        }
        this.hideSlashAutocomplete();
    }

    onInputKeydown(e) {
        const ac = this.elements.slashAutocomplete;
        if (ac.classList.contains('hidden')) return;

        const items = ac.querySelectorAll('.slash-autocomplete-item');
        if (!items.length) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.slashActiveIndex = Math.min(this.slashActiveIndex + 1, items.length - 1);
            this.highlightSlashItem(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.slashActiveIndex = Math.max(this.slashActiveIndex - 1, 0);
            this.highlightSlashItem(items);
        } else if (e.key === 'Tab' || e.key === 'Enter') {
            if (this.slashActiveIndex >= 0 && this.slashActiveIndex < items.length) {
                e.preventDefault();
                items[this.slashActiveIndex].click();
            }
        } else if (e.key === 'Escape') {
            this.hideSlashAutocomplete();
        }
    }

    showSlashAutocomplete(suggestions) {
        const ac = this.elements.slashAutocomplete;
        ac.innerHTML = '';
        this.slashActiveIndex = -1;

        suggestions.forEach((item, i) => {
            const el = document.createElement('div');
            el.className = 'slash-autocomplete-item';
            el.innerHTML = `<span class="slash-autocomplete-cmd">${item.cmd}</span><span class="slash-autocomplete-hint">${item.hint}</span>`;
            el.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.selectSlashItem(item);
            });
            ac.appendChild(el);
        });

        ac.classList.remove('hidden');
    }

    hideSlashAutocomplete() {
        this.elements.slashAutocomplete.classList.add('hidden');
        this.slashActiveIndex = -1;
    }

    highlightSlashItem(items) {
        items.forEach((el, i) => {
            el.classList.toggle('active', i === this.slashActiveIndex);
        });
    }

    selectSlashItem(item) {
        const suffix = item.suffix || '';
        this.elements.input.value = item.cmd + suffix;
        this.elements.input.focus();
        this.hideSlashAutocomplete();
    }

    scrollToBottom() {
        this.elements.messages.scrollTop = this.elements.messages.scrollHeight;
    }

    async fetchProjects() {
        try {
            const response = await fetch('/api/projects');
            const data = await response.json();
            this.projectNames = data.projects || [];
            this.updateProjectDropdown(this.projectNames);
        } catch (e) {
            console.error('Failed to fetch projects:', e);
        }
    }

    updateProjectDropdown(projects) {
        const select = this.elements.projectSelect;
        const currentValue = this.currentProject || '';

        // Clear existing options except first and last
        while (select.options.length > 2) {
            select.remove(1);
        }

        // Insert project options before the "+ New project" option
        const newProjectOption = select.options[select.options.length - 1];
        projects.forEach(project => {
            const option = document.createElement('option');
            option.value = project;
            option.textContent = project;
            select.insertBefore(option, newProjectOption);
        });

        // Set current selection
        if (currentValue && projects.includes(currentValue)) {
            select.value = currentValue;
        } else if (currentValue) {
            // Current project not in list, add it
            const option = document.createElement('option');
            option.value = currentValue;
            option.textContent = currentValue;
            select.insertBefore(option, newProjectOption);
            select.value = currentValue;
        }
    }

    handleProjectChange(e) {
        const value = e.target.value;

        if (value === '__new__') {
            this.promptNewProject();
            // Reset to placeholder if cancelled
            if (!this.currentProject) {
                e.target.value = '';
            } else {
                e.target.value = this.currentProject;
            }
            return;
        }

        if (value) {
            this.selectProject(value);
        }
    }

    promptNewProject() {
        const name = prompt('Enter new project name:');
        if (name && name.trim()) {
            this.selectProject(name.trim());
        }
    }

    selectProject(projectName) {
        // Haptic feedback if supported
        if (navigator.vibrate) {
            navigator.vibrate(10);
        }

        // Send /use command via WebSocket
        const message = `/use ${projectName}`;

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ message }));
        } else {
            this.sendHttp(message);
        }
    }

    openProjectSelector() {
        // Focus and open the project select dropdown
        this.elements.projectSelect.focus();
        // Programmatically open dropdown (works in some browsers)
        if (typeof this.elements.projectSelect.showPicker === 'function') {
            try {
                this.elements.projectSelect.showPicker();
            } catch (e) {
                // showPicker may fail in some contexts, fallback to focus
            }
        }
    }

    updateProjectBadge(project) {
        this.currentProject = project;
        // Update dropdown selection
        const select = this.elements.projectSelect;
        if (project) {
            // Check if option exists
            let found = false;
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === project) {
                    found = true;
                    break;
                }
            }
            if (!found) {
                // Add new option
                const option = document.createElement('option');
                option.value = project;
                option.textContent = project;
                const newProjectOption = select.options[select.options.length - 1];
                select.insertBefore(option, newProjectOption);
            }
            select.value = project;
        }
        // Refresh project list after any message completes
        this.fetchProjects();
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.chat = new LaibraryChat();
});
