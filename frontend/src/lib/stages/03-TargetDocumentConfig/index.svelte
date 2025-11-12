<script lang="ts">
	import { Accordion, SegmentedControl } from '@skeletonlabs/skeleton-svelte';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import { runState } from '$lib/shared/stores/run.svelte';
	import { toaster } from '$lib/shared/stores/toast';
	import { debounce } from '$lib/shared/utils/debounce';
	import { browser } from '$app/environment';
	import StyledTagsInput from './StyledTagsInput.svelte';
	import DocumentTypeCard from './DocumentTypeCard.svelte';
	import type { DocumentScrapingConfig } from './types';
	import { onMount } from 'svelte';

	let nextDocTypeId = 0;

	const defaultConfig: DocumentScrapingConfig = {
		globalTerms: [],
		sequentialSearchTerms: ['+2024'],
		documentTypes: [
			{ id: 0, name: 'Annual Report', terms: ['"annual report"'], fileType: 'pdf' },
			{ id: 1, name: 'Climate Report', terms: ['"climate report"'], fileType: 'pdf' },
			{
				id: 2,
				name: 'Sustainability Data Pack',
				terms: ['sustainability', '"data pack"', '"data book"', 'databook', 'datapack'],
				fileType: 'either'
			}
		],
		stripFromCompanyName: [' Ltd.', ' Inc.', ' Corp.', ' Pty.'],
		requiredInTitle: [],
		includeCompanyName: true,
		includeStockTicker: true,
		stripTickerSuffix: true,
		tryWithoutQuotes: true,
		withoutQuotesPreference: 'instead',
		engines: ['duckduckgo']
	};

	nextDocTypeId = defaultConfig.documentTypes.length;

	let config = $state<DocumentScrapingConfig>(JSON.parse(JSON.stringify(defaultConfig)));

	let newDocumentTypeName = $state('');

	// Derived counts for info card
	const docCount = $derived(config.documentTypes.length);
	const engineCount = $derived(config.engines.length || 1);
	const quoteMultiplier = $derived(
		config.tryWithoutQuotes ? (config.withoutQuotesPreference === 'instead' ? 1 : 2) : 1
	);
	const seqCount = $derived(Math.max(1, config.sequentialSearchTerms.length || 1));
	const perDoc = $derived(engineCount * quoteMultiplier * seqCount);
	const totalSearches = $derived(perDoc * docCount);

	async function saveConfig() {
		const projectDirectory = runState.state.projectName;
		if (!projectDirectory) {
			toaster.error({
				title: 'Project Not Found',
				description: 'The project directory has not been set. Please complete the import step.'
			});
			return false;
		}

		const configToSave = {
			...config,
			documentTypes: config.documentTypes.map(({ id, ...rest }) => rest)
		};

		console.log('Saving document configuration:', configToSave);
		try {
			const response = await fetch('/api/config/documents', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ project_directory: projectDirectory, config: configToSave })
			});

			if (!response.ok) {
				const errorData = await response.json();
				throw new Error(errorData.detail || 'Failed to save configuration');
			}

			// Invalidate subsequent steps on successful save
			for (let i = 3; i < runState.steps.length; i++) {
				runState.setStepValidity(i, false);
			}
			runState.state.maxStep = 3;

			toaster.success({
				title: 'Configuration Saved',
				description: 'Document configuration has been saved successfully.'
			});
			return true;
		} catch (error) {
			console.error('Error saving config:', error);
			toaster.error({
				title: 'Save Failed',
				description:
					error instanceof Error ? error.message : 'An unknown error occurred while saving.'
			});
			return false;
		}
	}

	const debouncedSave = debounce(saveConfig, 1000);

	// Suppress initial auto-save while loading existing config
	let suppressAutoSave = $state(true);

	$effect(() => {
		// Deeply watch config object for changes
		JSON.stringify(config);

		if (browser && !suppressAutoSave) {
			debouncedSave();
		}
	});

	// This step is considered valid as long as there's at least one document type.
	$effect(() => {
		runState.setStepValidity(2, config.documentTypes.length > 0);
	});

	function addDocumentType() {
		if (newDocumentTypeName.trim() === '') return;
		config.documentTypes.push({
			id: nextDocTypeId++,
			name: newDocumentTypeName,
			terms: [],
			fileType: 'pdf'
		});
		newDocumentTypeName = '';
	}

	function removeDocumentType(id: number) {
		config.documentTypes = config.documentTypes.filter((dt) => dt.id !== id);
	}

	function restoreDefaults() {
		// Create a deep copy to avoid shared references
		config = JSON.parse(JSON.stringify(defaultConfig));
		nextDocTypeId = config.documentTypes.length;
	}

	// Load existing document configuration on resume
	onMount(() => {
		console.log('Step 3 onMount: runState.state.projectName', runState.state.projectName);
		console.log('Step 3 onMount: runState.state.companyData', runState.state.companyData);
		const projectDirectory = runState.state.projectName;
		(async () => {
			if (!browser || !projectDirectory) {
				suppressAutoSave = false;
				return;
			}
			try {
				const res = await fetch(
					`/api/config/documents?project_directory=${encodeURIComponent(projectDirectory)}`
				);
				if (!res.ok) {
					suppressAutoSave = false; // Allow user edits to persist even if no config yet
					return;
				}
				const cfg = await res.json();

				// Adopt server values when defined; otherwise leave current values unchanged
				if (Array.isArray(cfg.sequentialSearchTerms))
					config.sequentialSearchTerms = cfg.sequentialSearchTerms;
				if (Array.isArray(cfg.documentTypes))
					config.documentTypes = cfg.documentTypes.map((dt: any) => ({
						id: nextDocTypeId++,
						name: dt.name,
						terms: dt.terms || [],
						fileType: dt.fileType === 'xlsx' ? 'xlsx' : 'pdf'
					}));
				if (Array.isArray(cfg.stripFromCompanyName))
					config.stripFromCompanyName = cfg.stripFromCompanyName;
				if (Array.isArray(cfg.requiredInTitle)) config.requiredInTitle = cfg.requiredInTitle;
				if (typeof cfg.includeCompanyName === 'boolean')
					config.includeCompanyName = cfg.includeCompanyName;
				if (typeof cfg.includeStockTicker === 'boolean')
					config.includeStockTicker = cfg.includeStockTicker;
				if (typeof cfg.stripTickerSuffix === 'boolean')
					config.stripTickerSuffix = cfg.stripTickerSuffix;
				if (typeof cfg.tryWithoutQuotes === 'boolean')
					config.tryWithoutQuotes = cfg.tryWithoutQuotes;
				if (['first', 'after', 'instead'].includes(cfg.withoutQuotesPreference))
					config.withoutQuotesPreference = cfg.withoutQuotesPreference;
				if (Array.isArray(cfg.engines)) config.engines = cfg.engines;
			} catch (e) {
				// Ignore and keep current config
			} finally {
				suppressAutoSave = false;
			}
		})();
	});
</script>

<div class="space-y-6">
	<div class="card preset-tonal space-y-4 p-4">
		<h3 class="h3">Search Operators</h3>
		<p class="text-surface-500 text-sm">
			Use operators for powerful searches:
			<span class="badge bg-green-500 text-white">+term</span> (required/strengthen),
			<span class="badge bg-red-500 text-white">-term</span> (exclude),
			<span class="badge bg-blue-500 text-white">"multi word"</span> (exact phrase),
			<span class="badge bg-purple-500 text-white">~"fuzzy"</span><a
				href="https://duckduckgo.com/duckduckgo-help-pages/results/syntax"
				target="_blank"
				rel="noopener noreferrer"
				class="anchor">(fuzzy search)</a
			>.
		</p>
	</div>

	<div class="card preset-tonal space-y-4 p-4">
		<h3 class="h3">Sequential Search Terms</h3>
		<p>
			These terms are tried in order and appended to the end of the base query (e.g. +2025, then
			+2024).
		</p>
		<StyledTagsInput
			bind:value={config.sequentialSearchTerms}
			name="sequential-terms"
			placeholder="Add a sequential term"
		/>
	</div>

	<Accordion collapsible>
		<Accordion.Item value="advanced-settings">
			<h3 class="h3 font-bold">
				<Accordion.ItemTrigger>
					<span>Advanced Search Settings</span>
					<Accordion.ItemIndicator class="group">
						<ChevronUp class="hidden size-4 group-data-[state=open]:block" />
						<ChevronDown class="block size-4 group-data-[state=open]:hidden" />
					</Accordion.ItemIndicator>
				</Accordion.ItemTrigger>
			</h3>
			<Accordion.ItemContent>
				<div class="grid grid-cols-1 gap-6 pt-4 md:grid-cols-2">
					<div class="space-y-2">
						<h4 class="h4">Required in title</h4>
						<p class="text-surface-500 mb-2 text-sm">
							Documents must have these terms in their title. Multi-word tags are automatically
							quoted.
						</p>
						<StyledTagsInput
							bind:value={config.requiredInTitle}
							name="title-terms"
							placeholder="Add title term..."
						/>
					</div>

					<div class="space-y-4">
						<h4 class="h4">Company Name Settings</h4>
						<div class="flex items-center space-x-4">
							<label class="flex items-center space-x-2">
								<input type="checkbox" class="checkbox" bind:checked={config.includeCompanyName} />
								<span>Include company name in search</span>
							</label>
							<label class="flex items-center space-x-2">
								<input type="checkbox" class="checkbox" bind:checked={config.includeStockTicker} />
								<span>Include stock ticker in search (as required +ticker)</span>
							</label>
						</div>
						{#if config.includeCompanyName}
							<div>
								<p class="text-surface-500 mb-2 text-sm">
									These suffixes will be removed from the company name before searching.
								</p>
								<StyledTagsInput
									bind:value={config.stripFromCompanyName}
									name="strip-terms"
									placeholder="Add suffix..."
								/>
							</div>
						{/if}

						{#if config.includeStockTicker}
							<div>
								<p class="text-surface-500 mb-2 text-sm">Stock ticker options</p>
								<label class="flex items-center space-x-2">
									<input type="checkbox" class="checkbox" bind:checked={config.stripTickerSuffix} />
									<span>Strip regional suffix (e.g. -AU) before searching</span>
								</label>
							</div>
						{/if}
					</div>

					<div class="space-y-4">
						<h4 class="h4">Quoting Strategy</h4>
						<label class="flex items-center space-x-2">
							<input type="checkbox" class="checkbox" bind:checked={config.tryWithoutQuotes} />
							<span>Try search without quotes</span>
						</label>
						{#if config.tryWithoutQuotes}
							<div class="flex items-center space-x-4">
								<label class="flex items-center gap-2 text-sm">
									<input
										type="radio"
										class="radio"
										bind:group={config.withoutQuotesPreference}
										value="first"
									/>
									<span>Before main search</span>
								</label>
								<label class="flex items-center gap-2 text-sm">
									<input
										type="radio"
										class="radio"
										bind:group={config.withoutQuotesPreference}
										value="after"
									/>
									<span>After main search</span>
								</label>
								<label class="flex items-center gap-2 text-sm">
									<input
										type="radio"
										class="radio"
										bind:group={config.withoutQuotesPreference}
										value="instead"
									/>
									<span>Instead of quoted</span>
								</label>
							</div>
						{/if}
					</div>

					<div class="space-y-2">
						<h4 class="h4">Search Engines</h4>
						<div class="flex flex-col gap-2">
							<label class="flex items-center space-x-2">
								<input
									type="checkbox"
									class="checkbox"
									bind:group={config.engines}
									value="duckduckgo"
								/>
								<span>DuckDuckGo</span>
							</label>
							<label class="flex items-center space-x-2">
								<input
									type="checkbox"
									class="checkbox"
									bind:group={config.engines}
									value="google"
								/>
								<span>Google</span>
							</label>
							<label class="flex items-center space-x-2">
								<input type="checkbox" class="checkbox" bind:group={config.engines} value="bing" />
								<span>Bing</span>
							</label>
						</div>
					</div>
				</div>
			</Accordion.ItemContent>
		</Accordion.Item>
	</Accordion>

	<div class="flex items-center justify-between">
		<h3 class="h3">Document Types</h3>
		<button type="button" class="btn preset-tonal-surface" onclick={restoreDefaults}>
			Restore Defaults
		</button>
	</div>

	<div class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
		{#each config.documentTypes as docType, i (docType.id)}
			<DocumentTypeCard
				bind:docType={config.documentTypes[i]}
				onRemove={() => removeDocumentType(docType.id)}
			/>
		{/each}

		<!-- Bottom two-column controls: Add new doc type and searches card -->
		<div class="col-span-full grid grid-cols-1 gap-4 md:grid-cols-2">
			<div class="card preset-glass-surface space-y-4 p-4">
				<h4 class="h4">Add New Document Type</h4>
				<div class="flex items-end gap-2">
					<label class="label w-full">
						<span>Name</span>
						<input
							class="input"
							type="text"
							bind:value={newDocumentTypeName}
							placeholder="e.g. ESG Report"
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									e.preventDefault();
									addDocumentType();
								}
							}}
						/>
					</label>
					<button type="button" class="btn preset-filled" onclick={addDocumentType}> Add </button>
				</div>
			</div>
			{#if docCount > 0}
				<div class="alert preset-tonal-warning space-y-2 p-4">
					<p class="font-semibold">This configuration contains:</p>
					<ul class="list">
						<li>
							<strong>{runState.state.companyData.processedCompanies?.length || 0}</strong> companies
						</li>
						<li><strong>{docCount}</strong> documents per company</li>
						<li><strong>{engineCount}</strong> search engine(s)</li>
						<li><strong>{seqCount}</strong> sequential term(s)</li>
						<li>
							<strong
								>{config.tryWithoutQuotes
									? config.withoutQuotesPreference === 'instead'
										? 'x1 (without quotes only)'
										: 'x2 (with and without quotes)'
									: 'x1 (quoted only)'}
							</strong>
						</li>
					</ul>
					<p>
						= <strong
							>{totalSearches *
								(runState.state.companyData.processedCompanies?.length || 0)}</strong
						> total searches
					</p>
				</div>
			{/if}
		</div>
	</div>
</div>
