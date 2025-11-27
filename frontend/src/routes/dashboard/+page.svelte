<script lang="ts">
	import { browser } from '$app/environment';
	import PlotlyChart from '$lib/components/PlotlyChart.svelte';
	import type { PageData } from './$types';

	export let data: PageData;

	type StageSummary = {
		total: number;
		searched: number;
		downloaded: number;
		extracted: number;
		analysed: number;
		verified: number;
	};

	type ScatterPoint = {
		scope_1?: number | null;
		net_income?: number | null;
		revenue?: number | null;
		ebitda?: number | null;
		assets?: number | null;
		employees?: number | null;
		net_zero_mentions?: number | null;
		profitability_ratio?: number | null;
		reputational_concern_ratio?: number | null;
		profitability_emissions_ratio?: number | null;
		ebitda_emissions_ratio?: number | null;
		net_zero_mentions_per_page?: number | null;
		industry?: string | null;
		company?: string | null;
		revenue_mm?: number | null;
	};

	type GroupMatrixRow = {
		group: string;
		cells: Array<{
			industry: string;
			count: number;
			emissions: number;
		}>;
	};

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
			stages: StageSummary;
			filtered_stages: StageSummary;
		};
		scope_averages?: Array<{ industry?: string | null; scope_1_avg?: number | null; scope_2_avg?: number | null }>;
		table?: Array<Record<string, unknown>>;
		scatter?: {
			scope1_vs_net_income?: ScatterPoint[];
			scope1_vs_revenue?: ScatterPoint[];
			scope1_vs_ebitda?: ScatterPoint[];
			scope1_vs_assets?: ScatterPoint[];
			scope1_vs_employees?: ScatterPoint[];
			scope1_vs_net_zero_mentions?: ScatterPoint[];
			scope1_vs_profitability_ratio?: ScatterPoint[];
			scope1_vs_reputational_concern_ratio?: ScatterPoint[];
			pe_vs_reputational_concern?: ScatterPoint[];
			ebitda_per_emissions_vs_mentions_per_page?: ScatterPoint[];
			ebitda_per_emissions_vs_total_mentions?: ScatterPoint[];
		};
		group_matrix?: {
			rows: GroupMatrixRow[];
			columns: string[];
		};
	};

	const stageDefaults: StageSummary = {
		total: 0,
		searched: 0,
		downloaded: 0,
		extracted: 0,
		analysed: 0,
		verified: 0
	};

	const stageOrder: Array<{ key: keyof StageSummary; label: string }> = [
		{ key: 'total', label: 'Total' },
		{ key: 'searched', label: 'Searched' },
		{ key: 'downloaded', label: 'Downloaded' },
		{ key: 'extracted', label: 'Extracted' },
		{ key: 'analysed', label: 'Analysed' },
		{ key: 'verified', label: 'Verified' }
	];

	type ScatterConfig = {
		id: string;
		label: string;
		key: keyof NonNullable<MetricsResponse['scatter']>;
		valueKey: keyof ScatterPoint;
		axisLabel: string;
		xKey?: keyof ScatterPoint;
		xAxisLabel?: string;
	};

	const chartTabs: ScatterConfig[] = [
		{
			id: 'net_income',
			label: 'Scope 1 + 2 vs Net Income',
			key: 'scope1_vs_net_income',
			valueKey: 'net_income',
			axisLabel: 'Net Income (MM AUD)'
		},
		{
			id: 'revenue',
			label: 'Scope 1 + 2 vs Revenue',
			key: 'scope1_vs_revenue',
			valueKey: 'revenue',
			axisLabel: 'Revenue (MM AUD)'
		},
		{
			id: 'ebitda',
			label: 'Scope 1 + 2 vs EBITDA',
			key: 'scope1_vs_ebitda',
			valueKey: 'ebitda',
			axisLabel: 'EBITDA (MM AUD)'
		},
		{
			id: 'assets',
			label: 'Scope 1 + 2 vs Total Assets',
			key: 'scope1_vs_assets',
			valueKey: 'assets',
			axisLabel: 'Total Assets (MM AUD)'
		},
		{
			id: 'employees',
			label: 'Scope 1 + 2 vs Employees',
			key: 'scope1_vs_employees',
			valueKey: 'employees',
			axisLabel: 'Employees (count)'
		},
		{
			id: 'net_zero_mentions',
			label: 'Scope 1 + 2 vs Net Zero Mentions',
			key: 'scope1_vs_net_zero_mentions',
			valueKey: 'net_zero_mentions',
			axisLabel: 'Net zero mentions (count)'
		},
		{
			id: 'profitability_ratio',
			label: 'Scope 1 + 2 vs Profitability',
			key: 'scope1_vs_profitability_ratio',
			valueKey: 'profitability_ratio',
			axisLabel: 'Net income / revenue'
		},
		{
			id: 'reputational_concern_ratio',
			label: 'Scope 1 + 2 vs Reputational Concern',
			key: 'scope1_vs_reputational_concern_ratio',
			valueKey: 'reputational_concern_ratio',
			axisLabel: 'Net zero mentions / revenue'
		},
		{
			id: 'pe_vs_reputation',
			label: 'P/E vs Reputational Concern',
			key: 'pe_vs_reputational_concern',
			valueKey: 'profitability_emissions_ratio',
			xKey: 'reputational_concern_ratio',
			axisLabel: 'P/E (Profitability / Emissions)',
			xAxisLabel: 'Reputational Concern (mentions/revenue)'
		},
		{
			id: 'ebitda_emissions_vs_mentions_density',
			label: 'EBITDA/Emissions vs Mentions/Page',
			key: 'ebitda_per_emissions_vs_mentions_per_page',
			valueKey: 'ebitda_emissions_ratio',
			xKey: 'net_zero_mentions_per_page',
			axisLabel: 'EBITDA ($) / Emissions',
			xAxisLabel: 'Mentions per Page'
		},
		{
			id: 'ebitda_emissions_vs_total_mentions',
			label: 'EBITDA/Emissions vs Total Mentions',
			key: 'ebitda_per_emissions_vs_total_mentions',
			valueKey: 'ebitda_emissions_ratio',
			xKey: 'net_zero_mentions',
			axisLabel: 'EBITDA ($) / Emissions',
			xAxisLabel: 'Total Net Zero Mentions'
		}
	];

	let metrics: MetricsResponse = data.metrics as MetricsResponse;
	let loading = false;
	let errorMessage = '';

	let selectedIndustries: string[] = [];
	let selectedRbics: string[] = [];
	let selectedStates: string[] = [];
	let selectedMethods: string[] = [];
	let scope1MinInput = metrics.ranges.scope1?.[0]?.toString() ?? '';
	let scope1MaxInput = metrics.ranges.scope1?.[1]?.toString() ?? '';

	let activeChart = chartTabs[0].id;
	let useLogScale = false;

	const plotConfig = { responsive: true, displaylogo: false };

	function normaliseStage(summary?: StageSummary | null): StageSummary {
		return {
			total: summary?.total ?? 0,
			searched: summary?.searched ?? 0,
			downloaded: summary?.downloaded ?? 0,
			extracted: summary?.extracted ?? 0,
			analysed: summary?.analysed ?? 0,
			verified: summary?.verified ?? 0
		};
	}

	function buildScatter(
		dataset: ScatterPoint[],
		yKey: keyof ScatterPoint,
		yLabel: string,
		xKey: keyof ScatterPoint = 'scope_1',
		xLabel: string = 'Scope 1 + 2'
	) {
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
			x: points.map((p) => Number(p[xKey] ?? 0)),
			y: points.map((p) => Number(p[yKey] ?? 0)),
			text: points.map(
				(p) =>
					`${p.company ?? 'Unknown'}<br>${xLabel}: ${formatNumber(
						(p as any)[xKey] ?? null
					)}<br>${yLabel}: ${formatNumber((p as any)[yKey] ?? null)}`
			),
			marker: {
				size: 9,
				opacity: 0.85
			},
			hovertemplate: '%{text}<extra></extra>'
		}));
	}

	function scatterLayout(
		title: string,
		yLabel: string,
		xLabel: string = 'Scope 1 + 2 (kgCO₂e)',
		logScale: boolean = false
	) {
		return {
			title,
			margin: { t: 50, r: 30, b: 60, l: 70 },
			xaxis: {
				title: xLabel,
				hoverformat: ',.0f',
				type: logScale ? 'log' : 'linear'
			},
			yaxis: {
				title: yLabel,
				hoverformat: ',.0f',
				type: logScale ? 'log' : 'linear'
			},
			legend: { orientation: 'h', x: 0, y: -0.2 }
		};
	}

	$: overallStages = normaliseStage(metrics.summary?.stages);
	$: filteredStages = normaliseStage(metrics.summary?.filtered_stages);

	$: scatterConfigs = chartTabs.reduce(
		(acc, tab) => {
			const dataset = (metrics.scatter?.[tab.key] as ScatterPoint[] | undefined) ?? [];
			acc[tab.id] = {
				data: buildScatter(
					dataset,
					tab.valueKey,
					tab.axisLabel,
					tab.xKey,
					tab.xAxisLabel
				),
				layout: scatterLayout(
					tab.label,
					tab.axisLabel,
					tab.xAxisLabel,
					useLogScale
				)
			};
			return acc;
		},
		{} as Record<string, { data: Array<Record<string, unknown>>; layout: Record<string, unknown> }>
	);

	$: activeChartConfig = scatterConfigs[activeChart] ?? { data: [], layout: scatterLayout('', '', useLogScale) };

	$: groupMatrixRows = metrics.group_matrix?.rows ?? [];
	$: groupMatrixColumns = metrics.group_matrix?.columns ?? [];

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
</script>

<section class="space-y-10">
	<header class="space-y-2">
		<h1 class="text-3xl font-semibold text-slate-800">Dashboard</h1>
		<p class="text-sm text-slate-500">
			Overview of extraction status and key indicators across the portfolio.
		</p>
	</header>

	<section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
		{#each stageOrder as stage}
			<div class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
				<p class="text-xs font-semibold uppercase tracking-wide text-slate-500">{stage.label}</p>
				<p class="mt-2 text-3xl font-semibold text-slate-900">
					{formatNumber(overallStages[stage.key] ?? 0)}
				</p>
				<p class="mt-1 text-xs text-slate-500">
					Filtered: {formatNumber(filteredStages[stage.key] ?? 0)}
				</p>
			</div>
		{/each}
	</section>

	<details class="rounded-xl border border-slate-200 bg-white shadow-sm" open>
		<summary class="cursor-pointer select-none rounded-xl px-5 py-4 text-sm font-semibold text-slate-700">
			Filters
		</summary>
		<div class="space-y-4 px-5 pb-5">
			<p class="text-xs text-slate-500">
				Adjust filters and press reset to return to the original dataset.
			</p>

			{#if errorMessage}
				<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
					{errorMessage}
				</div>
			{/if}

			<div class="grid gap-4 md:grid-cols-2">
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">Industries</span>
					<select class="input h-32" multiple bind:value={selectedIndustries} on:change={refreshMetrics}>
						{#each metrics.filters?.industries ?? [] as option}
							<option value={option}>{option}</option>
						{/each}
					</select>
				</label>
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">RBICS sectors</span>
					<select class="input h-32" multiple bind:value={selectedRbics} on:change={refreshMetrics}>
						{#each metrics.filters?.rbics ?? [] as option}
							<option value={option}>{option}</option>
						{/each}
					</select>
				</label>
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">States</span>
					<select class="input h-32" multiple bind:value={selectedStates} on:change={refreshMetrics}>
						{#each metrics.filters?.states ?? [] as option}
							<option value={option}>{option}</option>
						{/each}
					</select>
				</label>
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">Analysis methods</span>
					<select class="input h-32" multiple bind:value={selectedMethods} on:change={refreshMetrics}>
						{#each metrics.filters?.methods ?? [] as option}
							<option value={option}>{option}</option>
						{/each}
					</select>
				</label>
			</div>

			<div class="grid gap-4 md:grid-cols-2">
				<label class="space-y-2 text-sm text-slate-600">
					<span class="font-semibold text-slate-700">Scope 1 + 2 range (kgCO₂e)</span>
					<div class="flex gap-3">
						<input class="input" type="number" placeholder="Min" bind:value={scope1MinInput} on:change={refreshMetrics} />
						<input class="input" type="number" placeholder="Max" bind:value={scope1MaxInput} on:change={refreshMetrics} />
					</div>
				</label>
			</div>

			<div class="flex items-center gap-3">
				<button class="btn preset-filled" type="button" on:click={resetFilters} disabled={loading}>
					Reset filters
				</button>
				{#if loading}
					<span class="text-sm text-slate-500">Refreshing metrics…</span>
				{/if}
			</div>
		</div>
	</details>

	<section class="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
		<div class="flex flex-wrap items-center justify-between gap-4">
			<div class="flex flex-wrap items-center gap-2">
				{#each chartTabs as tab}
					<button
						class="rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors"
						class:bg-slate-900={activeChart === tab.id}
						class:text-white={activeChart === tab.id}
						class:border-slate-900={activeChart === tab.id}
						class:bg-white={activeChart !== tab.id}
						class:text-slate-700={activeChart !== tab.id}
						class:border-slate-200={activeChart !== tab.id}
						on:click={() => (activeChart = tab.id)}
						type="button"
					>
						{tab.label}
					</button>
				{/each}
			</div>
			<label class="flex items-center gap-2 text-sm text-slate-700">
				<input
					type="checkbox"
					bind:checked={useLogScale}
					class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
				/>
				<span class="font-medium">Log scale</span>
			</label>
		</div>
		<div class="rounded-lg border border-slate-100 bg-white p-3">
			{#if activeChartConfig.data.length}
				<PlotlyChart data={activeChartConfig.data} layout={activeChartConfig.layout} config={plotConfig} />
			{:else}
				<p class="px-2 py-6 text-sm text-slate-500">
					Not enough data to render this chart for the selected filters.
				</p>
			{/if}
		</div>
	</section>

	<section class="rounded-xl border border-slate-200 bg-white shadow-sm">
		<header class="border-b border-slate-200 px-5 py-4">
			<h2 class="text-lg font-semibold text-slate-900">Filtered companies</h2>
			<p class="text-sm text-slate-500">
				All companies matching the filters. Scroll to view the full list.
			</p>
		</header>
		<div class="overflow-x-auto px-5 pb-5">
			<div class="max-h-[32rem] overflow-y-auto">
				<table class="min-w-full divide-y divide-slate-200 text-sm">
					<thead class="sticky top-0 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
						<tr>
							<th class="px-3 py-2 font-semibold">Ticker</th>
							<th class="px-3 py-2 font-semibold">Name</th>
							<th class="px-3 py-2 text-right font-semibold">Scope 1 (kgCO₂e)</th>
							<th class="px-3 py-2 text-right font-semibold">Scope 2 (kgCO₂e)</th>
							<th class="px-3 py-2 font-semibold">Reporting group</th>
							<th class="px-3 py-2 text-right font-semibold">Revenue (MM AUD)</th>
							<th class="px-3 py-2 font-semibold">Industry</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-100 bg-white">
						{#if (metrics.table ?? []).length === 0}
							<tr>
								<td class="px-3 py-6 text-center text-slate-400" colspan="7">
									No companies match the selected filters.
								</td>
							</tr>
						{:else}
							{#each metrics.table ?? [] as row}
								<tr>
									<td class="px-3 py-2 font-mono text-xs text-slate-600">
										{(row.ticker as string) ?? '—'}
									</td>
									<td class="px-3 py-2 text-slate-800">
										{(row.name as string) ?? (row.ticker as string) ?? '—'}
									</td>
									<td class="px-3 py-2 text-right text-slate-800">
										{formatNumber((row.scope_1 as number) ?? null)}
									</td>
									<td class="px-3 py-2 text-right text-slate-800">
										{formatNumber((row.scope_2 as number) ?? null)}
									</td>
									<td class="px-3 py-2 text-slate-600">
										{(row.reporting_group as string) ?? '—'}
									</td>
									<td class="px-3 py-2 text-right text-slate-800">
										{formatNumber((row.revenue_mm as number) ?? null)}
									</td>
									<td class="px-3 py-2 text-slate-600">
										{(row.anzsic_division as string) ?? '—'}
									</td>
								</tr>
							{/each}
						{/if}
					</tbody>
				</table>
			</div>
		</div>
	</section>

	<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
		<h3 class="text-lg font-semibold text-slate-900">Reporting group vs industry</h3>
		{#if groupMatrixRows.length}
			<div class="overflow-x-auto">
				<table class="min-w-full divide-y divide-slate-200 text-sm">
					<thead class="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
						<tr>
							<th class="px-3 py-2 font-semibold">Reporting group</th>
							{#each groupMatrixColumns as column}
								<th class="px-3 py-2 font-semibold">{column}</th>
							{/each}
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-100 bg-white">
						{#each groupMatrixRows as row}
							<tr>
								<td class="px-3 py-3 text-slate-800">{row.group}</td>
								{#each groupMatrixColumns as column}
									{@const cell = row.cells.find((entry) => entry.industry === column)}
									<td class="px-3 py-3 text-slate-700">
										<div class="font-semibold text-slate-800">
											{formatNumber(cell?.count ?? 0)} companies
										</div>
										<div class="text-xs text-slate-500">
											{formatNumber(cell?.emissions ?? null)} kg scope 1
										</div>
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="text-sm text-slate-500">No aggregation data available for this selection.</p>
		{/if}
	</section>

	<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
		<h3 class="text-lg font-semibold text-slate-900">Average scope emissions by industry</h3>
		{#if (metrics.scope_averages ?? []).length === 0}
			<p class="text-sm text-slate-500">No industry averages available for this selection.</p>
		{:else}
			<table class="w-full text-sm">
				<thead class="text-left text-xs uppercase tracking-wide text-slate-600">
					<tr>
						<th class="pb-2 font-semibold">Industry</th>
						<th class="pb-2 text-right font-semibold">Scope 1 (kgCO₂e)</th>
						<th class="pb-2 text-right font-semibold">Scope 2 (kgCO₂e)</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-slate-100">
					{#each metrics.scope_averages ?? [] as row}
						<tr>
							<td class="py-2 text-slate-700">{row.industry ?? '—'}</td>
							<td class="py-2 text-right text-slate-800">
								{formatNumber(row.scope_1_avg ?? null)}
							</td>
							<td class="py-2 text-right text-slate-800">
								{formatNumber(row.scope_2_avg ?? null)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
</section>
