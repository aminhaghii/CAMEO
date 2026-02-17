function importApp() {
    return {
        step: 'upload',
        dragOver: false,
        selectedFile: null,
        uploading: false,
        uploadError: '',
        batchId: null,
        pollTimer: null,
        statusFilename: '',
        statusTotal: 0,
        statusProcessed: 0,
        summary: null,
        columnMappingResult: null,
        ingestionMeta: null,

        // Phase 2 interactive inventory state
        inventoryRows: [],
        activeFilter: 'TOTAL',
        analyzeLoading: false,

        showEditModal: false,
        showDeleteModal: false,
        editMode: 'add',
        editTarget: null,
        deleteTarget: null,

        editForm: {
            staging_id: null,
            row_version: '',
            chemical_id: null,
            name: '',
            cas: '',
            quantity: '',
            unit: '',
            location: '',
            notes: '',
        },

        chemicalSearchQuery: '',
        chemicalSearchResults: [],

        get progressPct() {
            if (!this.statusTotal) return 0;
            return Math.round((this.statusProcessed / this.statusTotal) * 100);
        },

        get matchRatePct() {
            return this.summary ? Math.round((this.summary.match_rate || 0) * 100) : 0;
        },

        get matchRateColor() {
            const p = this.matchRatePct;
            return p >= 80 ? 'text-emerald-600' : p >= 50 ? 'text-amber-600' : 'text-rose-600';
        },

        get matchRateBarColor() {
            const p = this.matchRatePct;
            return p >= 80 ? 'bg-emerald-500' : p >= 50 ? 'bg-amber-500' : 'bg-rose-500';
        },

        get filteredRows() {
            if (this.activeFilter === 'TOTAL') return this.inventoryRows;
            if (this.activeFilter === 'CONFIRMED') {
                return this.inventoryRows.filter(r => this.statusBucket(r.match_status) === 'CONFIRMED');
            }
            if (this.activeFilter === 'REVIEW') {
                return this.inventoryRows.filter(r => this.statusBucket(r.match_status) === 'REVIEW');
            }
            if (this.activeFilter === 'REJECTED') {
                return this.inventoryRows.filter(r => this.statusBucket(r.match_status) === 'REJECTED');
            }
            return this.inventoryRows;
        },

        get statusCounts() {
            const counts = { total: this.inventoryRows.length, confirmed: 0, review: 0, rejected: 0 };
            this.inventoryRows.forEach((row) => {
                const bucket = this.statusBucket(row.match_status);
                if (bucket === 'CONFIRMED') counts.confirmed += 1;
                else if (bucket === 'REVIEW') counts.review += 1;
                else if (bucket === 'REJECTED') counts.rejected += 1;
            });
            return counts;
        },

        get activeFilterLabel() {
            if (this.activeFilter === 'CONFIRMED') return 'Confirmed';
            if (this.activeFilter === 'REVIEW') return 'Review Required';
            if (this.activeFilter === 'REJECTED') return 'Rejected';
            return 'All Rows';
        },

        get canAnalyze() {
            if (!this.inventoryRows.length) return false;
            const unresolved = this.inventoryRows.some(r => this.statusBucket(r.match_status) !== 'CONFIRMED');
            return !unresolved;
        },

        get analyzeDisabledReason() {
            if (!this.inventoryRows.length) return 'No chemicals to analyze.';
            const unresolved = this.inventoryRows.filter(r => this.statusBucket(r.match_status) !== 'CONFIRMED').length;
            if (unresolved > 0) return `Resolve ${unresolved} non-confirmed row(s) before analysis.`;
            return 'All rows confirmed. Ready to run compatibility analysis.';
        },

        statusBucket(matchStatus) {
            const s = (matchStatus || '').toUpperCase();
            if (s === 'MATCHED' || s === 'CONFIRMED') return 'CONFIRMED';
            if (s === 'REVIEW_REQUIRED' || s === 'REVIEW') return 'REVIEW';
            return 'REJECTED';
        },

        statusLabel(matchStatus) {
            const b = this.statusBucket(matchStatus);
            if (b === 'CONFIRMED') return 'CONFIRMED';
            if (b === 'REVIEW') return 'REVIEW';
            return 'REJECTED';
        },

        statusChipClass(matchStatus) {
            const b = this.statusBucket(matchStatus);
            if (b === 'CONFIRMED') return 'bg-emerald-100 text-emerald-700';
            if (b === 'REVIEW') return 'bg-amber-100 text-amber-700';
            return 'bg-rose-100 text-rose-700';
        },

        setFilter(filter) {
            this.activeFilter = filter;
        },

        formatSize(bytes) {
            if (!bytes) return '';
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / 1048576).toFixed(1)} MB`;
        },

        handleDrop(e) {
            this.dragOver = false;
            if (e.dataTransfer.files.length > 0) this.selectedFile = e.dataTransfer.files[0];
        },

        handleFileSelect(e) {
            if (e.target.files.length > 0) this.selectedFile = e.target.files[0];
        },

        async startUpload() {
            if (!this.selectedFile) return;
            this.uploading = true;
            this.uploadError = '';
            const formData = new FormData();
            formData.append('file', this.selectedFile);

            try {
                const res = await fetch('/api/inventory/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (!res.ok || data.error) {
                    this.uploadError = data.error || 'Upload failed';
                    this.uploading = false;
                    return;
                }
                this.batchId = data.batch_id;
                this.statusFilename = data.filename;
                this.step = 'processing';
                this.startPolling();
            } catch (e) {
                this.uploadError = `Upload failed: ${e.message}`;
            } finally {
                this.uploading = false;
            }
        },

        startPolling() {
            this.pollTimer = setInterval(() => this.pollStatus(), 1500);
        },

        async pollStatus() {
            if (!this.batchId) return;
            try {
                const res = await fetch(`/api/inventory/status/${this.batchId}`);
                const data = await res.json();
                this.statusTotal = data.total_rows || 0;
                this.statusProcessed = data.processed || 0;

                if (data.status === 'completed') {
                    clearInterval(this.pollTimer);
                    this.summary = data.summary || null;
                    await Promise.all([
                        this.loadColumnMapping(),
                        this.loadInventoryRows(),
                    ]);
                    this.step = 'results';
                } else if (data.status === 'error') {
                    clearInterval(this.pollTimer);
                    this.uploadError = data.error_msg || 'Processing failed';
                    this.step = 'upload';
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        },

        async loadColumnMapping() {
            if (!this.batchId) return;
            try {
                const res = await fetch(`/api/inventory/column_mapping/${this.batchId}`);
                const data = await res.json();
                this.columnMappingResult = data.column_mapping || null;
                this.ingestionMeta = data.ingestion_meta || null;
            } catch (e) {
                console.error('Column mapping fetch error:', e);
            }
        },

        async loadInventoryRows() {
            if (!this.batchId) return;
            try {
                const res = await fetch(`/api/inventory/rows/${this.batchId}`);
                const data = await res.json();
                this.inventoryRows = data.rows || [];
                this.syncSummaryFromRows();
            } catch (e) {
                console.error('Rows fetch error:', e);
            }
        },

        syncSummaryFromRows() {
            if (!this.summary) return;
            const counts = this.statusCounts;
            this.summary.total_rows = counts.total;
            this.summary.matched = counts.confirmed;
            this.summary.review_required = counts.review;
            this.summary.unidentified = counts.rejected;
            this.summary.match_rate = counts.total ? counts.confirmed / counts.total : 0;
        },

        openAddModal() {
            this.editMode = 'add';
            this.editTarget = null;
            this.editForm = {
                staging_id: null,
                row_version: '',
                chemical_id: null,
                name: '',
                cas: '',
                quantity: '',
                unit: '',
                location: '',
                notes: '',
            };
            this.chemicalSearchQuery = '';
            this.chemicalSearchResults = [];
            this.showEditModal = true;
        },

        openEditModal(row) {
            this.editMode = 'edit';
            this.editTarget = row;
            this.editForm = {
                staging_id: row.staging_id,
                row_version: row.row_version || '',
                chemical_id: row.chemical_id || null,
                name: row.name || '',
                cas: row.cas || '',
                quantity: row.quantity || '',
                unit: row.unit || '',
                location: row.location || '',
                notes: row.notes || '',
            };
            this.chemicalSearchQuery = '';
            this.chemicalSearchResults = [];
            this.showEditModal = true;
        },

        closeEditModal() {
            this.showEditModal = false;
        },

        openDeleteModal(row) {
            this.deleteTarget = row;
            this.showDeleteModal = true;
        },

        closeDeleteModal() {
            this.showDeleteModal = false;
            this.deleteTarget = null;
        },

        async searchChemicals() {
            const query = (this.chemicalSearchQuery || '').trim();
            if (query.length < 2) {
                this.chemicalSearchResults = [];
                return;
            }
            try {
                const res = await fetch(`/api/inventory/search_chemicals?q=${encodeURIComponent(query)}`);
                const data = await res.json();
                this.chemicalSearchResults = data.results || [];
            } catch (e) {
                console.error('Chemical search error:', e);
            }
        },

        selectChemical(result) {
            this.editForm.chemical_id = result.chemical_id;
            this.editForm.name = result.chemical_name || '';
            this.editForm.cas = result.cas || '';
            this.chemicalSearchQuery = result.chemical_name || '';
            this.chemicalSearchResults = [];
        },

        async saveEditModal() {
            try {
                if (this.editMode === 'add') {
                    if (!this.editForm.chemical_id) {
                        alert('Please select a chemical from search results.');
                        return;
                    }

                    const res = await fetch('/api/inventory/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            batch_id: this.batchId,
                            chemical_id: this.editForm.chemical_id,
                            quantity: this.editForm.quantity,
                            unit: this.editForm.unit,
                            location: this.editForm.location,
                            notes: this.editForm.notes,
                        }),
                    });
                    const data = await res.json();
                    if (!res.ok || data.error) {
                        alert(data.error || 'Add failed');
                        return;
                    }

                    this.inventoryRows.push(data.row);
                    this.inventoryRows.sort((a, b) => a.row_index - b.row_index);
                    if (data.warning) alert(data.warning);
                } else {
                    const res = await fetch('/api/inventory/edit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            batch_id: this.batchId,
                            staging_id: this.editForm.staging_id,
                            row_version: this.editForm.row_version,
                            chemical_id: this.editForm.chemical_id,
                            quantity: this.editForm.quantity,
                            unit: this.editForm.unit,
                            location: this.editForm.location,
                            notes: this.editForm.notes,
                        }),
                    });
                    const data = await res.json();
                    if (!res.ok || data.error) {
                        alert(data.error || 'Edit failed');
                        return;
                    }

                    this.inventoryRows = this.inventoryRows.map((r) =>
                        r.staging_id === data.row.staging_id ? data.row : r
                    );
                }

                this.syncSummaryFromRows();
                this.closeEditModal();
            } catch (e) {
                alert(`Save failed: ${e.message}`);
            }
        },

        async confirmDelete() {
            if (!this.deleteTarget) return;
            try {
                const res = await fetch(`/api/inventory/delete/${this.deleteTarget.staging_id}?batch_id=${encodeURIComponent(this.batchId)}`, {
                    method: 'DELETE',
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    alert(data.error || 'Delete failed');
                    return;
                }

                this.inventoryRows = this.inventoryRows.filter((r) => r.staging_id !== this.deleteTarget.staging_id);
                this.syncSummaryFromRows();
                this.closeDeleteModal();
            } catch (e) {
                alert(`Delete failed: ${e.message}`);
            }
        },

        async analyzeInventory() {
            if (!this.canAnalyze || this.analyzeLoading) return;
            this.analyzeLoading = true;
            try {
                const res = await fetch('/api/inventory/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ batch_id: this.batchId }),
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    alert(data.error || 'Analysis failed');
                    return;
                }
                window.location.href = `/inventory/analysis/${encodeURIComponent(this.batchId)}`;
            } catch (e) {
                alert(`Analysis failed: ${e.message}`);
            } finally {
                this.analyzeLoading = false;
            }
        },

        reset() {
            this.step = 'upload';
            this.dragOver = false;
            this.selectedFile = null;
            this.uploading = false;
            this.uploadError = '';
            this.batchId = null;
            this.summary = null;
            this.columnMappingResult = null;
            this.ingestionMeta = null;
            this.statusTotal = 0;
            this.statusProcessed = 0;
            this.inventoryRows = [];
            this.activeFilter = 'TOTAL';
            this.analyzeLoading = false;
            this.showEditModal = false;
            this.showDeleteModal = false;
            this.editTarget = null;
            this.deleteTarget = null;
            this.chemicalSearchQuery = '';
            this.chemicalSearchResults = [];
            if (this.pollTimer) clearInterval(this.pollTimer);
        },
    };
}
