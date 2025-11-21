<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';

	type PlotlyElement = HTMLDivElement & {
		data?: Array<Record<string, any>>;
		on?: (eventName: string, listener: (event: any) => boolean | void) => void;
		removeListener?: (eventName: string, listener: (event: any) => void) => void;
		removeAllListeners?: (eventName?: string) => void;
	};

	type TraceState = 'active' | 'dimmed' | 'focused';

	type LegendHighlightingOptions = {
		enabled: boolean;
		dimOpacity: number;
		focusLineWidth?: number;
		focusMarkerSizeMultiplier: number;
	};

	type TraceStyleSnapshot = {
		opacity?: number;
		lineWidth?: number;
		markerSize?: number;
	};

	const defaultLegendHighlighting: LegendHighlightingOptions = {
		enabled: true,
		dimOpacity: 0.2,
		focusLineWidth: 4,
		focusMarkerSizeMultiplier: 1.4
	};

	export let data: Array<Record<string, unknown>> = [];
	export let layout: Record<string, unknown> = {};
	export let config: Record<string, unknown> = { responsive: true, displaylogo: false };
	export let legendHighlighting: boolean | Partial<LegendHighlightingOptions> = true;

	let Plotly: any;
	let container: HTMLDivElement | null = null;
	let initialized = false;
	let pending = false;
	let plotElement: PlotlyElement | null = null;
let legendOptions: LegendHighlightingOptions = defaultLegendHighlighting;
	let traceStates: TraceState[] = [];
	let traceStyles: TraceStyleSnapshot[] = [];
	let removeLegendHandlers: (() => void) | null = null;
let legendOptionsSignature = JSON.stringify(defaultLegendHighlighting);

	$: legendOptions = normalizeLegendHighlighting(legendHighlighting);

	async function ensurePlotly() {
		if (!browser || Plotly) {
			return;
		}
		const module = await import('plotly.js-dist-min');
		Plotly = module.default ?? module;
	}

	async function render(
		currentData: Array<Record<string, unknown>> = data,
		currentLayout: Record<string, unknown> = layout,
		currentConfig: Record<string, unknown> = config
	) {
		if (!browser || !Plotly || !container) {
			return;
		}
		if (pending) {
			return;
		}
		pending = true;
		try {
			if (!initialized) {
				await Plotly.newPlot(container, currentData, currentLayout, currentConfig);
				initialized = true;
			} else {
				await Plotly.react(container, currentData, currentLayout, currentConfig);
			}
			plotElement = container as PlotlyElement;
			snapshotTraces();
		} finally {
			pending = false;
		}
	}

	function normalizeLegendHighlighting(
		value: boolean | Partial<LegendHighlightingOptions>
	): LegendHighlightingOptions {
		if (value === false) {
			return { ...defaultLegendHighlighting, enabled: false };
		}
		if (value === true) {
			return { ...defaultLegendHighlighting };
		}
		return { ...defaultLegendHighlighting, ...value, enabled: value.enabled ?? true };
	}

	function snapshotTraces() {
		if (!plotElement?.data) {
			traceStates = [];
			traceStyles = [];
			return;
		}
		traceStyles = plotElement.data.map((trace: Record<string, any>) => ({
			opacity: typeof trace.opacity === 'number' ? trace.opacity : undefined,
			lineWidth: typeof trace.line?.width === 'number' ? trace.line.width : undefined,
			markerSize: typeof trace.marker?.size === 'number' ? trace.marker.size : undefined
		}));
		traceStates = traceStyles.map(() => 'active');
	}

	function syncLegendHandlers() {
		cleanupLegendHandlers();

		if (!plotElement?.on || !legendOptions.enabled) {
			return;
		}

		const handleLegendClick = (eventData: any) => {
			if (!legendOptions.enabled) {
				return true;
			}

			const traceIndex = eventData?.curveNumber ?? eventData?.traceIndex;
			if (traceIndex == null) {
				return true;
			}

			const modifierActive = Boolean(
				eventData?.event?.shiftKey || eventData?.event?.metaKey || eventData?.event?.ctrlKey
			);

			if (modifierActive) {
				focusTrace(traceIndex);
			} else {
				toggleDim(traceIndex);
			}

			eventData?.event?.preventDefault?.();
			return false;
		};

		const handleLegendDoubleClick = (eventData: any) => {
			if (!legendOptions.enabled) {
				return true;
			}
			resetTraceStates();
			eventData?.event?.preventDefault?.();
			return false;
		};

		plotElement.on('plotly_legendclick', handleLegendClick);
		plotElement.on('plotly_doubleclick', handleLegendDoubleClick);

		removeLegendHandlers = () => {
			plotElement?.removeListener?.('plotly_legendclick', handleLegendClick);
			plotElement?.removeListener?.('plotly_doubleclick', handleLegendDoubleClick);
		};
	}

	function cleanupLegendHandlers() {
		if (removeLegendHandlers) {
			removeLegendHandlers();
			removeLegendHandlers = null;
		}
	}

	function toggleDim(index: number) {
		traceStates[index] = traceStates[index] === 'dimmed' ? 'active' : 'dimmed';
		applyTraceState(index);
	}

	function focusTrace(activeIndex: number) {
		traceStates = traceStates.map((_, index) => (index === activeIndex ? 'focused' : 'dimmed'));
		applyAllTraceStates();
	}

	function resetTraceStates() {
		traceStates = traceStates.map(() => 'active');
		applyAllTraceStates();
	}

	function applyAllTraceStates() {
		traceStates.forEach((_, index) => applyTraceState(index));
	}

	function applyTraceState(index: number) {
		if (!Plotly || !container) {
			return;
		}
		const snapshot = traceStyles[index];
		const state = traceStates[index];

		if (!snapshot) {
			return;
		}

		const updates: Record<string, unknown[]> = {};

		if (state === 'active') {
			if (snapshot.opacity !== undefined) {
				updates.opacity = [snapshot.opacity];
			} else {
				updates.opacity = [1];
			}
			updates['line.width'] = [snapshot.lineWidth ?? null];
			updates['marker.size'] = [snapshot.markerSize ?? null];
		}

		if (state === 'dimmed') {
			updates.opacity = [legendOptions.dimOpacity];
		}

		if (state === 'focused') {
			updates.opacity = [1];
			if (snapshot.lineWidth !== undefined && legendOptions.focusLineWidth !== undefined) {
				updates['line.width'] = [Math.max(snapshot.lineWidth, legendOptions.focusLineWidth)];
			} else if (legendOptions.focusLineWidth !== undefined) {
				updates['line.width'] = [legendOptions.focusLineWidth];
			}

			if (snapshot.markerSize !== undefined && legendOptions.focusMarkerSizeMultiplier !== 1) {
				updates['marker.size'] = [snapshot.markerSize * legendOptions.focusMarkerSizeMultiplier];
			}
		}

		Plotly.restyle(container, updates, [index]);
	}

	$: if (plotElement) {
		syncLegendHandlers();
	}

	$: if (traceStates.length) {
		const nextSignature = JSON.stringify(legendOptions);
		if (legendOptionsSignature !== nextSignature) {
			legendOptionsSignature = nextSignature;
			if (legendOptions.enabled) {
				applyAllTraceStates();
			} else {
				resetTraceStates();
			}
		}
	}

	onMount(async () => {
		if (!browser) return;
		await ensurePlotly();
		await render();
	});

	onDestroy(() => {
		cleanupLegendHandlers();
		if (Plotly && container) {
			Plotly.purge(container);
		}
	});

	$: if (browser && Plotly && container) {
		render(data, layout, config);
	}
</script>

<div class="w-full min-h-[640px]" bind:this={container}></div>
