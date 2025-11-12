<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';

	const { steps } = runState;

	function isCurrentStep(index: number) {
		return runState.state.currentStep === index;
	}
</script>

<div class="w-full">
	<div class="relative">
		<div class="flex items-center justify-between gap-4">
			{#each steps as step, i (step)}
				<!-- Numeral Item -->
				<div
					class="flex h-8 w-8 items-center justify-center rounded-full font-bold
            {isCurrentStep(i) ? 'bg-primary-300-700' : 'bg-surface-300-700'} "
				>
					<span>{i + 1}</span>
				</div>
			{/each}
		</div>
		<!-- Line -->
		<hr class="hr absolute left-0 right-0 top-[50%] z-[-1]" />
	</div>
</div>
