<script lang="ts">
	import { browser } from '$app/environment';
	import { onMount } from 'svelte';
import type { PageData } from './$types';

	type VerificationPayload = {
		key: string;
		identity: { ticker?: string | null; name?: string | null };
		verification: {
			status?: string;
			verified_at?: string | null;
			notes?: string | null;
			overrides?: {
				scope_1?: number | null;
				scope_2?: number | null;
				scope_3?: number | null;
			};
		};
		emissions: {
			scope_1?: number | null;
			scope_2?: number | null;
			scope_3?: number | null;
			scope_1_confidence?: number | null;
			scope_2_confidence?: number | null;
		};
		annotations: {
			reporting_group?: string | null;
			location?: string | null;
			rbics_sector?: string | null;
		};
		analysis: {
			method?: string | null;
			confidence?: number | null;
			snippet_label?: string | null;
			snippet_pages?: number[];
		};
		snippet: {
			path?: string | null;
			text?: string | null;
		};
		previews: Array<{ page: number; data_url: string }>;
	};

	export let data: PageData;

	let currentKey: string | null = data.initialKey;
	let company: VerificationPayload | null = (data.initialCompany as VerificationPayload | null) ?? null;
	let message = '';
	let notes = '';
	let loading = false;
	let replacementUrl = '';
	let overrideScope1 = '';
	let overrideScope2 = '';
	let overrideScope3 = '';
	let uploadError = '';
	let uploadContents: string | null = null;
	let uploadFilename: string | null = null;
let availableMethods: string[] = (data.methodOptions as string[] | undefined) ?? [];
let selectedMethods: string[] = availableMethods.length > 0 ? [...availableMethods] : [];

	async function fetchNext(options: { skip?: boolean } = {}) {
		const params = new URLSearchParams();
		if (currentKey) {
			params.set('current_key', currentKey);
		}
		if (options.skip) {
			params.set('skip_current', 'true');
		}
	for (const method of selectedMethods) {
		if (method) params.append('methods', method);
	}
		const res = await fetch(`/api/verification/next?${params.toString()}`);
		if (!res.ok) {
			throw new Error('Failed to load next company.');
		}
		const payload = (await res.json()) as { key: string | null };
		await loadCompany(payload.key);
	}

	async function loadCompany(key: string | null) {
		currentKey = key;
		if (!key) {
			company = null;
			return;
		}
		const detailRes = await fetch(`/api/verification/${encodeURIComponent(key)}`);
		if (!detailRes.ok) {
			throw new Error('Failed to load verification target.');
		}
		company = (await detailRes.json()) as VerificationPayload;
		notes = company?.verification?.notes ?? '';
		overrideScope1 = company?.verification?.overrides?.scope_1?.toString() ?? '';
		overrideScope2 = company?.verification?.overrides?.scope_2?.toString() ?? '';
		overrideScope3 = company?.verification?.overrides?.scope_3?.toString() ?? '';
		replacementUrl = '';
		uploadContents = null;
		uploadFilename = null;
		uploadError = '';
	}

	async function acceptCompany() {
		if (!currentKey) return;
		loading = true;
		try {
		const query = new URLSearchParams();
		for (const method of selectedMethods) {
			if (method) query.append('methods', method);
		}
		const suffix = query.toString() ? `?${query.toString()}` : '';
			const res = await fetch(
			`/api/verification/${encodeURIComponent(currentKey)}/accept${suffix}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ notes })
				}
			);
			if (!res.ok) {
				const detail = await res.json().catch(() => ({}));
				throw new Error(detail.detail ?? 'Failed to accept verification.');
			}
			const payload = (await res.json()) as {
				message?: string;
				next_key?: string | null;
			};
			message = payload.message ?? 'Verification accepted.';
			notes = '';
			await loadCompany(payload.next_key ?? null);
		} catch (err) {
			if (err instanceof Error) {
				message = err.message;
			} else {
				message = 'Unexpected error while accepting verification.';
			}
		} finally {
			loading = false;
		}
	}

	async function rejectCompany() {
		if (!currentKey) return;
		loading = true;
		try {
			const payload: Record<string, unknown> = {
				notes: notes || null,
				replacement_url: replacementUrl || null
			};
			if (uploadContents) {
				payload.upload_contents = uploadContents;
				payload.upload_filename = uploadFilename;
			}
		const query = new URLSearchParams();
		for (const method of selectedMethods) {
			if (method) query.append('methods', method);
		}
		const suffix = query.toString() ? `?${query.toString()}` : '';
			const res = await fetch(
			`/api/verification/${encodeURIComponent(currentKey)}/reject${suffix}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(payload)
				}
			);
			if (!res.ok) {
				const detail = await res.json().catch(() => ({}));
				throw new Error(detail.detail ?? 'Failed to reject verification.');
			}
			const response = (await res.json()) as { message?: string; next_key?: string | null };
			message = response.message ?? 'Verification rejected.';
			notes = '';
			replacementUrl = '';
			uploadContents = null;
			uploadFilename = null;
			await loadCompany(response.next_key ?? null);
		} catch (err) {
			message = err instanceof Error ? err.message : 'Unexpected error while rejecting verification.';
		} finally {
			loading = false;
		}
	}

	async function saveOverride() {
		if (!currentKey) return;
		const scope1 = Number(overrideScope1);
		const scope2 = Number(overrideScope2);
		const scope3 = overrideScope3.trim() ? Number(overrideScope3) : null;
		if (!Number.isFinite(scope1) || !Number.isFinite(scope2)) {
			message = 'Provide numeric values for Scope 1 and Scope 2 overrides.';
			return;
		}
		if (scope3 !== null && !Number.isFinite(scope3)) {
			message = 'Scope 3 override must be numeric if provided.';
			return;
		}
		loading = true;
		try {
		const query = new URLSearchParams();
		for (const method of selectedMethods) {
			if (method) query.append('methods', method);
		}
		const suffix = query.toString() ? `?${query.toString()}` : '';
			const res = await fetch(
			`/api/verification/${encodeURIComponent(currentKey)}/override${suffix}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({
						scope_1: scope1,
						scope_2: scope2,
						scope_3: scope3,
						notes: notes || null
					})
				}
			);
			if (!res.ok) {
				const detail = await res.json().catch(() => ({}));
				throw new Error(detail.detail ?? 'Failed to save manual override.');
			}
			const response = (await res.json()) as { message?: string; next_key?: string | null };
			message = response.message ?? 'Manual corrections saved.';
			notes = '';
			overrideScope1 = '';
			overrideScope2 = '';
			overrideScope3 = '';
			await loadCompany(response.next_key ?? null);
		} catch (err) {
			message = err instanceof Error ? err.message : 'Unexpected error while saving manual override.';
		} finally {
			loading = false;
		}
	}

	async function skipCompany() {
		loading = true;
		try {
			await fetchNext({ skip: true });
			message = 'Skipped current company.';
		} catch (err) {
			if (err instanceof Error) {
				message = err.message;
			} else {
				message = 'Unexpected error while skipping company.';
			}
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		if (!browser) return;
		if (!company && currentKey) {
			loadCompany(currentKey).catch((err) => {
				message = err instanceof Error ? err.message : 'Failed to load company.';
			});
		}
	});

	function formatValue(value: number | null | undefined): string {
		if (value === null || value === undefined) return '—';
		return value.toLocaleString();
	}

	async function handleFileChange(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		const file = target.files?.[0];
		uploadContents = null;
		uploadFilename = null;
		uploadError = '';
		if (!file) {
			return;
		}
		if (file.type !== 'application/pdf') {
			uploadError = 'Only PDF files are supported.';
			return;
		}
		try {
		await new Promise<void>((resolve, reject) => {
			const reader = new FileReader();
			reader.onerror = () => reject(reader.error ?? new Error('Failed to read uploaded file.'));
			reader.onload = () => {
				const result = reader.result;
				if (typeof result === 'string') {
					uploadContents = result;
					uploadFilename = file.name;
					resolve();
				} else {
					reject(new Error('Unexpected file reader result.'));
				}
			};
			reader.readAsDataURL(file);
		});
		} catch (err) {
			uploadError = err instanceof Error ? err.message : 'Failed to read uploaded file.';
		}
	}

function updateMethodSelection(event: Event) {
	const target = event.currentTarget as HTMLSelectElement;
	selectedMethods = Array.from(target.selectedOptions).map((option) => option.value);
	fetchNext();
}
</script>

<section class="mx-auto max-w-7xl space-y-6 px-6 py-8">
	<header class="space-y-2">
		<h1 class="text-3xl font-semibold text-slate-900">Verification</h1>
		<p class="text-sm text-slate-600">
			Review extracted emissions values, attach notes, and progress the verification queue.
		</p>
	</header>

	{#if message}
		<div class="flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
			<p>{message}</p>
			<button
				class="rounded-md border border-blue-300 px-3 py-1 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
				type="button"
				onclick={() => (message = '')}
			>
				Dismiss
			</button>
		</div>
	{/if}

	<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
		<h2 class="text-lg font-semibold text-slate-900">Queue filters</h2>
		<p class="text-sm text-slate-500">
			Restrict the verification queue to specific analysis methods. Leave unselected to include all methods.
		</p>
		{#if availableMethods.length === 0}
			<p class="text-sm text-slate-500">No analysis methods available.</p>
		{:else}
			<select
				class="input h-40 w-full"
				multiple
				bind:value={selectedMethods}
				onchange={updateMethodSelection}
			>
				{#each availableMethods as method}
					<option value={method}>{method}</option>
				{/each}
			</select>
		{/if}
	</section>

	{#if !company}
		<div class="rounded-xl border border-slate-200 bg-white p-6 text-center text-slate-600 shadow-sm">
			<p>No companies are waiting for verification. Great job!</p>
		</div>
	{:else}
		<section class="space-y-6">
			<div class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
				<header>
					<h2 class="text-2xl font-semibold text-slate-900">
						{company.identity.name ?? company.identity.ticker ?? 'Unknown company'}
					</h2>
					<p class="text-sm text-slate-600">
						Status: {company.verification.status ?? 'pending'}
						{#if company.verification.verified_at}
							· Last updated {company.verification.verified_at}
						{/if}
					</p>
				</header>

				<div class="grid gap-4 md:grid-cols-3">
					<div>
						<h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500">Scope 1</h3>
						<p class="text-lg font-semibold text-slate-900">
							{formatValue(company.emissions.scope_1)}
						</p>
					</div>
					<div>
						<h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500">Scope 2</h3>
						<p class="text-lg font-semibold text-slate-900">
							{formatValue(company.emissions.scope_2)}
						</p>
					</div>
					<div>
						<h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500">Scope 3</h3>
						<p class="text-lg font-semibold text-slate-900">
							{formatValue(company.emissions.scope_3)}
						</p>
					</div>
				</div>

				<div class="grid gap-4 md:grid-cols-3">
					<div>
						<h4 class="text-xs uppercase tracking-wide text-slate-500">Reporting group</h4>
						<p class="text-sm text-slate-700">
							{company.annotations.reporting_group ?? '—'}
						</p>
					</div>
					<div>
						<h4 class="text-xs uppercase tracking-wide text-slate-500">Location</h4>
						<p class="text-sm text-slate-700">{company.annotations.location ?? '—'}</p>
					</div>
					<div>
						<h4 class="text-xs uppercase tracking-wide text-slate-500">Analysis method</h4>
						<p class="text-sm text-slate-700">{company.analysis.method ?? '—'}</p>
					</div>
				</div>
			</div>

			<section class="grid gap-4 md:grid-cols-2">
				<div class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
					<h3 class="text-lg font-semibold text-slate-900">Snippet</h3>
					{#if company.snippet.text}
						<pre class="max-h-72 overflow-y-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-800">
{company.snippet.text}
						</pre>
					{:else}
						<p class="text-sm text-slate-500">No snippet available for this record.</p>
					{/if}
				</div>
				<div class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
					<h3 class="text-lg font-semibold text-slate-900">Preview</h3>
					{#if company.previews.length > 0}
						<div class="space-y-4">
							{#each company.previews as preview}
								<figure class="space-y-2">
									<figcaption class="text-xs uppercase text-slate-500">
										Page {preview.page}
									</figcaption>
									<img
										alt={`Preview page ${preview.page}`}
										class="w-full rounded border border-slate-200 bg-white shadow-sm"
										src={preview.data_url}
									/>
								</figure>
							{/each}
						</div>
					{:else}
						<p class="text-sm text-slate-500">Preview unavailable.</p>
					{/if}
				</div>
			</section>

			<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
				<label class="block space-y-2">
					<span class="text-sm font-medium text-slate-700">Reviewer notes</span>
					<textarea
						class="input min-h-[120px]"
						rows="4"
						bind:value={notes}
						placeholder="Document any rationale or follow-up actions..."
					/>
				</label>

				<div class="flex flex-wrap gap-3">
					<button
						class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
						disabled={loading}
						onclick={acceptCompany}
					>
						Accept
					</button>
					<button
						class="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60"
						disabled={loading}
						onclick={rejectCompany}
					>
						Reject
					</button>
					<button
						class="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
						disabled={loading}
						onclick={skipCompany}
					>
						Skip
					</button>
				</div>
			</section>

			<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
				<h3 class="text-lg font-semibold text-slate-900">Replacement document</h3>
				<p class="text-sm text-slate-500">
					Provide a new PDF URL or upload a replacement document before rejecting an extraction.
				</p>
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">Replacement PDF URL</span>
					<input
						class="input"
						type="url"
						placeholder="https://example.com/report.pdf"
						bind:value={replacementUrl}
					/>
				</label>
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">Upload PDF</span>
					<input class="input" type="file" accept="application/pdf" onchange={handleFileChange} />
					{#if uploadFilename}
						<p class="text-xs text-slate-500">Selected: {uploadFilename}</p>
					{/if}
					{#if uploadError}
						<p class="text-xs text-error-500">{uploadError}</p>
					{/if}
				</label>
			</section>

			<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
				<h3 class="text-lg font-semibold text-slate-900">Manual overrides</h3>
				<p class="text-sm text-slate-500">
					Update the extracted values when verified manually. Scope 1 and Scope 2 are required.
				</p>
				<div class="grid gap-4 md:grid-cols-3">
					<label class="space-y-2 text-sm text-slate-600">
						<span class="font-semibold text-slate-700">Scope 1 (kgCO₂e)</span>
						<input class="input" type="number" min="0" bind:value={overrideScope1} />
					</label>
					<label class="space-y-2 text-sm text-slate-600">
						<span class="font-semibold text-slate-700">Scope 2 (kgCO₂e)</span>
						<input class="input" type="number" min="0" bind:value={overrideScope2} />
					</label>
					<label class="space-y-2 text-sm text-slate-600">
						<span class="font-semibold text-slate-700">Scope 3 (kgCO₂e)</span>
						<input class="input" type="number" min="0" bind:value={overrideScope3} />
					</label>
				</div>
				<div>
					<button
						class="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
						disabled={loading}
						onclick={saveOverride}
					>
						Save manual override
					</button>
				</div>
			</section>
		</section>
	{/if}
</section>
