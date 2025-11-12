<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';

	export let data: Array<Record<string, unknown>> = [];
	export let layout: Record<string, unknown> = {};
	export let config: Record<string, unknown> = { responsive: true, displaylogo: false };

	let Plotly: any;
	let container: HTMLDivElement | null = null;
	let initialized = false;
	let pending = false;

	async function ensurePlotly() {
		if (!browser || Plotly) {
			return;
		}
		const module = await import('plotly.js-dist-min');
		Plotly = module.default ?? module;
	}

	async function render() {
		if (!browser || !Plotly || !container) {
			return;
		}
		if (pending) {
			return;
		}
		pending = true;
		try {
			if (!initialized) {
				await Plotly.newPlot(container, data, layout, config);
				initialized = true;
			} else {
				await Plotly.react(container, data, layout, config);
			}
		} finally {
			pending = false;
		}
	}

	onMount(async () => {
		if (!browser) return;
		await ensurePlotly();
		await render();
	});

	onDestroy(() => {
		if (Plotly && container) {
			Plotly.purge(container);
		}
	});

	$: if (browser && Plotly && container) {
		render();
	}
</script>

<div class="w-full min-h-[320px]" bind:this={container}></div>
