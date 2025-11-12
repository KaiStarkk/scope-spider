<script lang="ts">
	import { browser } from '$app/environment';
	import { onMount } from 'svelte';
	import PlotlyChart from '$lib/components/PlotlyChart.svelte';
	import type { PageData } from './$types';

	export let data: PageData;

	const { stats } = data.dashboard;

	type MetricsResponse = {
		filters: {
			industries: string[];
			rbics: string[];
			states: string[];
			methods: string[];
		};
		ranges: {
			scope1?: [number, number];
			net_income?: [number, number];
			revenue?: [number, number];
		};
		summary: {
			total_companies: number;
			filtered_companies: number;
		};
		top_revenue?: Array<{ name?: string | null; revenue_mm?: number | null; anzsic_division?: string | null }>;
		scope_averages?: Array<{ industry?: string | null; scope_1_avg?: number | null; scope_2_avg?: number | null }>;
		table?: Array<Record<string, unknown>>;
		scatter?: {
			scope1_vs_net_income?: ScatterPoint[];
			scope1_vs_revenue?: ScatterPoint[];
			scope1_vs_ebitda?: ScatterPoint[];
			scope1_vs_assets?: ScatterPoint[];
		};
		group_matrix?: {
			rows: string[];
			columns: string[];
			counts: number[][];
			emissions: number[][];
		};
	};

	type ScatterPoint = {
		scope_1?: number | null;
		net_income?: number | null;
		revenue?: number | null;
		ebitda?: number | null;
		assets?: number | null;
		industry?: string | null;
		company?: string | null;
		revenue_mm?: number | null;
	};

	let metrics: MetricsResponse = data.metrics as MetricsResponse;
	let loading = false;
	let errorMessage = '';

	let selectedIndustries: string[] = [];
	let selectedRbics: string[] = [];
	let selectedStates: string[] = [];
	let selectedMethods: string[] = [];
	let scope1MinInput = metrics.ranges.scope1?.[0]?.toString() ?? '';
	let scope1MaxInput = metrics.ranges.scope1?.[1]?.toString() ?? '';

	const plotConfig = { responsive: true, displaylogo: false };

	function markerSize(revenueMm: number | null | undefined): number {
		if (!revenueMm || Number.isNaN(revenueMm)) return 8;
		return Math.max(6, Math.sqrt(Math.abs(revenueMm)) + 6);
	}

	function buildScatter(dataset: ScatterPoint[], yKey: keyof ScatterPoint, yLabel: string) {
		const groups = new Map<string, ScatterPoint[]>();
		for (const point of dataset) {
			const industry = (point.industry ?? 'Unknown').toString();
			if (!groups.has(industry)) groups.set(industry, []);
			groups.get(industry)!.push(point);
		}
		return Array.from(groups.entries()).map(([industry, points]) => ({
			name: industry,
			type: 'scatter',
			mode: 'markers',
			x: points.map((p) => Number(p.scope_1 ?? 0)),
			y: points.map((p) => Number(p[yKey] ?? 0)),
			text: points.map(
				(p) => `${p.company ?? 'Unknown'}<br>Scope 1: ${formatNumber(p.scope_1 ?? null)}<br>${yLabel}: ${
					formatNumber((p as any)[yKey] ?? null)
				}`
			),
			marker: {
				size: points.map((p) => markerSize(p.revenue_mm ?? null)),
				opacity: 0.85
			},
			hovertemplate: '%{text}<extra></extra>'
		}));
	}

	function scatterLayout(title: string, yLabel: string) {
		return {
			title,
			margin: { t: 50, r: 30, b: 60, l: 70 },
			xaxis: { title: 'Scope 1 (kgCO₂e)', hoverformat: ',.0f' },
			yaxis: { title: yLabel, hoverformat: ',.0f' },
			legend: { orientation: 'h', x: 0, y: -0.2 }
		};
	}

	$: scatterNetIncomeData = buildScatter(metrics.scatter?.scope1_vs_net_income ?? [], 'net_income', 'Net Income');
	$: scatterNetIncomeLayout = scatterLayout('Scope 1 vs Net Income', 'Net Income (MM AUD)');

	$: scatterRevenueData = buildScatter(metrics.scatter?.scope1_vs_revenue ?? [], 'revenue', 'Revenue');
	$: scatterRevenueLayout = scatterLayout('Scope 1 vs Revenue', 'Revenue (MM AUD)');

	$: scatterEbitdaData = buildScatter(metrics.scatter?.scope1_vs_ebitda ?? [], 'ebitda', 'EBITDA');
	$: scatterEbitdaLayout = scatterLayout('Scope 1 vs EBITDA', 'EBITDA (MM AUD)');

	$: scatterAssetsData = buildScatter(metrics.scatter?.scope1_vs_assets ?? [], 'assets', 'Total Assets');
	$: scatterAssetsLayout = scatterLayout('Scope 1 vs Total Assets', 'Total Assets (MM AUD)');

	$: revenueBarData = [
		{
			type: 'bar',
			x: (metrics.top_revenue ?? []).map((item) => item.name ?? 'Unknown'),
			y: (metrics.top_revenue ?? []).map((item) => Number(item.revenue_mm ?? 0)),
			text: (metrics.top_revenue ?? []).map(
				(item) => `${item.name ?? 'Unknown'}<br>${formatNumber(item.revenue_mm ?? null)} MM AUD`
			),
			hovertemplate: '%{text}<extra></extra>',
			marker: { color: '#2563eb' }
		}
	];
	$: revenueBarLayout = {
		title: 'Top 10 Companies by Revenue',
		margin: { t: 50, r: 30, b: 80, l: 70 },
		xaxis: { automargin: true },
		yaxis: { title: 'Revenue (MM AUD)', hoverformat: ',.0f' }
	};

	const emptyMatrix = { rows: [] as string[], columns: [] as string[], counts: [] as number[][], emissions: [] as number[][] };
	$: groupMatrix = metrics.group_matrix ?? emptyMatrix;
	$: heatmapData = groupMatrix.rows.length && groupMatrix.columns.length
		? [
				{
					type: 'heatmap',
					x: groupMatrix.columns,
					y: groupMatrix.rows,
					z: groupMatrix.counts,
					text: groupMatrix.counts.map((row, i) =>
						row.map(
							(count, j) =>
								`${count} companies<br>${formatNumber(groupMatrix.emissions?.[i]?.[j] ?? null)} kg`
						)
					),
					hovertemplate: '%{text}<extra></extra>',
					colorscale: 'Blues'
				}
			]
		: [];
	$: heatmapLayout = {
		title: 'Companies & Scope 1 Emissions by Reporting Group / Industry',
		margin: { t: 60, r: 40, b: 80, l: 120 },
		xaxis: { automargin: true },
		yaxis: { automargin: true }
	};

	async function refreshMetrics() {
		if (!browser) return;
		loading = true;
		errorMessage = '';
		try {
			const params = new URLSearchParams();
			for (const value of selectedIndustries) params.append('industries', value);
			for (const value of selectedRbics) params.append('rbics', value);
			for (const value of selectedStates) params.append('states', value);
			for (const value of selectedMethods) params.append('methods', value);
			const min = scope1MinInput.trim();
			const max = scope1MaxInput.trim();
			if (min && max && !Number.isNaN(Number(min)) && !Number.isNaN(Number(max))) {
				params.set('scope1_min', Number(min).toString());
				params.set('scope1_max', Number(max).toString());
			}
			const response = await fetch(`/api/dashboard/metrics?${params.toString()}`);
			if (!response.ok) {
				const detail = await response.json().catch(() => ({}));
				throw new Error(detail.detail ?? 'Failed to load dashboard metrics.');
			}
			metrics = (await response.json()) as MetricsResponse;
		} catch (err) {
			errorMessage = err instanceof Error ? err.message : 'Unexpected error while loading metrics.';
		} finally {
			loading = false;
		}
	}

	function formatNumber(value: number | null | undefined): string {
		if (value === null || value === undefined || Number.isNaN(value)) return '—';
		if (!Number.isFinite(value)) return '—';
		if (Math.abs(value) >= 1_000_000) {
			return `${(value / 1_000_000).toFixed(1)}M`;
		}
		if (Math.abs(value) >= 1_000) {
			return `${(value / 1_000).toFixed(1)}k`;
		}
		return value.toLocaleString();
	}

	function resetFilters() {
		selectedIndustries = [];
		selectedRbics = [];
		selectedStates = [];
		selectedMethods = [];
	scope1MinInput = metrics.ranges.scope1?.[0]?.toString() ?? '';
	scope1MaxInput = metrics.ranges.scope1?.[1]?.toString() ?? '';
		refreshMetrics();
	}

	onMount(() => {
		if (!browser) return;
		refreshMetrics();
	});
</script>

<section class="space-y-8">
	<header class="space-y-2">
		<h1 class="text-3xl font-semibold text-surface-900">Dashboard</h1>
		<p class="text-sm text-surface-500">
			Overview of extracted emissions data and reporting coverage.
		</p>
	</header>

	<section class="grid gap-4 md:grid-cols-4">
		<div class="card preset-tonal p-4">
			<h2 class="text-sm uppercase tracking-wide text-surface-500">Total companies</h2>
			<p class="text-3xl font-semibold text-surface-900">{stats.total}</p>
		</div>
		<div class="card preset-tonal p-4">
			<h2 class="text-sm uppercase tracking-wide text-surface-500">Verified</h2>
			<p class="text-3xl font-semibold text-success-600">{stats.verified}</p>
		</div>
		<div class="card preset-tonal p-4">
			<h2 class="text-sm uppercase tracking-wide text-surface-500">Pending</h2>
			<p class="text-3xl font-semibold text-warning-600">{stats.pending}</p>
		</div>
		<div class="card preset-tonal p-4">
			<h2 class="text-sm uppercase tracking-wide text-surface-500">Filtered set</h2>
			<p class="text-3xl font-semibold text-surface-900">
				{metrics.summary?.filtered_companies ?? '—'}
			</p>
		</div>
	</section>

	<section class="card preset-elevated p-6 space-y-4">
		<header>
			<h2 class="text-lg font-semibold text-surface-900">Filters</h2>
			<p class="text-sm text-surface-500">
				Refine the dataset before downloading or graphing. Leave blank to include all values.
			</p>
		</header>

		{#if errorMessage}
			<div class="alert preset-tonal-error">
				<span>{errorMessage}</span>
			</div>
		{/if}

		<div class="grid gap-4 md:grid-cols-2">
			<label class="space-y-2 text-sm text-surface-600">
				<span class="font-semibold text-surface-700">Industries</span>
				<select class="select h-32" multiple bind:value={selectedIndustries} on:change={refreshMetrics}>
					{#each metrics.filters?.industries ?? [] as option}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</label>
			<label class="space-y-2 text-sm text-surface-600">
				<span class="font-semibold text-surface-700">RBICS Sectors</span>
				<select class="select h-32" multiple bind:value={selectedRbics} on:change={refreshMetrics}>
					{#each metrics.filters?.rbics ?? [] as option}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</label>
			<label class="space-y-2 text-sm text-surface-600">
				<span class="font-semibold text-surface-700">States</span>
				<select class="select h-32" multiple bind:value={selectedStates} on:change={refreshMetrics}>
					{#each metrics.filters?.states ?? [] as option}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</label>
			<label class="space-y-2 text-sm text-surface-600">
				<span class="font-semibold text-surface-700">Analysis methods</span>
				<select class="select h-32" multiple bind:value={selectedMethods} on:change={refreshMetrics}>
					{#each metrics.filters?.methods ?? [] as option}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</label>
		</div>

		<div class="grid gap-4 md:grid-cols-2">
			<label class="flex flex-col gap-2 text-sm text-surface-600">
				<span class="font-semibold text-surface-700">Scope 1 range (kgCO₂e)</span>
				<div class="flex gap-3">
					<input class="input" type="number" placeholder="min" bind:value={scope1MinInput} on:change={refreshMetrics} />
					<input class="input" type="number" placeholder="max" bind:value={scope1MaxInput} on:change={refreshMetrics} />
				</div>
			</label>
		</div>

		<div class="flex items-center gap-3">
			<button class="btn preset-tonal" type="button" on:click={resetFilters} disabled={loading}>
				Reset filters
			</button>
			{#if loading}
				<span class="text-sm text-surface-500">Refreshing metrics…</span>
			{/if}
		</div>
	</section>

	<section class="grid gap-6 md:grid-cols-2">
		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Scope 1 vs Net Income</h3>
			{#if scatterNetIncomeData.length}
				<PlotlyChart data={scatterNetIncomeData} layout={scatterNetIncomeLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">
					Not enough data to plot Net Income vs Scope 1 for the current filters.
				</p>
			{/if}
		</div>
		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Scope 1 vs Revenue</h3>
			{#if scatterRevenueData.length}
				<PlotlyChart data={scatterRevenueData} layout={scatterRevenueLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">
					Not enough data to plot Revenue vs Scope 1 for the current filters.
				</p>
			{/if}
		</div>
	</section>

	<section class="grid gap-6 md:grid-cols-2">
		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Scope 1 vs EBITDA</h3>
			{#if scatterEbitdaData.length}
				<PlotlyChart data={scatterEbitdaData} layout={scatterEbitdaLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">
					Not enough data to plot EBITDA vs Scope 1 for the current filters.
				</p>
			{/if}
		</div>
		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Scope 1 vs Total Assets</h3>
			{#if scatterAssetsData.length}
				<PlotlyChart data={scatterAssetsData} layout={scatterAssetsLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">
					Not enough data to plot Total Assets vs Scope 1 for the current filters.
				</p>
			{/if}
		</div>
	</section>

	<section class="card preset-elevated overflow-hidden">
		<header class="border-b border-surface-200 px-4 py-3">
			<h2 class="text-lg font-semibold text-surface-900">Filtered companies</h2>
			<p class="text-sm text-surface-500">
				Data returned by the current filter set.
			</p>
		</header>
		<div class="overflow-x-auto">
			<table class="min-w-full divide-y divide-surface-200 text-sm">
				<thead class="bg-surface-50 text-left text-xs uppercase tracking-wide text-surface-500">
					<tr>
						<th class="px-4 py-2">Ticker</th>
						<th class="px-4 py-2">Name</th>
						<th class="px-4 py-2 text-right">Scope 1 (kgCO₂e)</th>
						<th class="px-4 py-2 text-right">Scope 2 (kgCO₂e)</th>
						<th class="px-4 py-2">Reporting group</th>
						<th class="px-4 py-2 text-right">Revenue (MM AUD)</th>
						<th class="px-4 py-2">Industry</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-surface-100 bg-white">
					{#if (metrics.table ?? []).length === 0}
						<tr>
							<td class="px-4 py-4 text-center text-surface-400" colspan="7">
								No companies match the selected filters.
							</td>
						</tr>
					{:else}
						{#each (metrics.table ?? []).slice(0, 20) as row}
							<tr>
								<td class="px-4 py-3 font-mono text-xs text-surface-600">
									{(row.ticker as string) ?? '—'}
								</td>
								<td class="px-4 py-3 text-surface-900">
									{(row.name as string) ?? (row.ticker as string) ?? '—'}
								</td>
								<td class="px-4 py-3 text-right text-surface-900">
									{formatNumber((row.scope_1 as number) ?? null)}
								</td>
								<td class="px-4 py-3 text-right text-surface-900">
									{formatNumber((row.scope_2 as number) ?? null)}
								</td>
								<td class="px-4 py-3 text-surface-600">
									{(row.reporting_group as string) ?? '—'}
								</td>
								<td class="px-4 py-3 text-right text-surface-900">
									{formatNumber((row.revenue_mm as number) ?? null)}
								</td>
								<td class="px-4 py-3 text-surface-600">
									{(row.anzsic_division as string) ?? '—'}
								</td>
							</tr>
						{/each}
					{/if}
				</tbody>
			</table>
		</div>
	</section>

	<section class="grid gap-6 md:grid-cols-2">
		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Top revenue (MM AUD)</h3>
			{#if revenueBarData[0]?.x?.length}
				<PlotlyChart data={revenueBarData} layout={revenueBarLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">No revenue data available for this selection.</p>
			{/if}
		</div>

		<div class="card preset-tonal p-5 space-y-3">
			<h3 class="text-lg font-semibold text-surface-900">Reporting group vs industry</h3>
			{#if heatmapData.length}
				<PlotlyChart data={heatmapData} layout={heatmapLayout} config={plotConfig} />
			{:else}
				<p class="text-sm text-surface-500">No aggregation data available for this selection.</p>
			{/if}
		</div>
	</section>

	<section class="card preset-tonal p-5 space-y-3">
		<h3 class="text-lg font-semibold text-surface-900">Average scope emissions by industry</h3>
		{#if (metrics.scope_averages ?? []).length === 0}
			<p class="text-sm text-surface-500">No industry averages available for this selection.</p>
		{:else}
			<table class="w-full text-sm">
				<thead class="text-left text-xs uppercase tracking-wide text-surface-500">
					<tr>
						<th class="pb-2">Industry</th>
						<th class="pb-2 text-right">Scope 1 (kgCO₂e)</th>
						<th class="pb-2 text-right">Scope 2 (kgCO₂e)</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-surface-200">
					{#each (metrics.scope_averages ?? []).slice(0, 10) as row}
						<tr>
							<td class="py-2 text-surface-700">{row.industry ?? '—'}</td>
							<td class="py-2 text-right text-surface-900">
								{formatNumber(row.scope_1_avg ?? null)}
							</td>
							<td class="py-2 text-right text-surface-900">
								{formatNumber(row.scope_2_avg ?? null)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
</section>
