<script lang="ts">
	import { models, settings } from '$lib/stores';
	import { getContext } from 'svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import { getModelDisplayName, getModelShortDescription } from '$lib/utils/model-display';
	const i18n = getContext('i18n');

	export let selectedModels = [''];

	const pinModelHandler = async (modelId) => {
		let pinnedModels = $settings?.pinnedModels ?? [];

		if (pinnedModels.includes(modelId)) {
			pinnedModels = pinnedModels.filter((id) => id !== modelId);
		} else {
			pinnedModels = [...new Set([...pinnedModels, modelId])];
		}

		settings.set({ ...$settings, pinnedModels: pinnedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	$: if ($models.length > 0) {
		const availableModelIds = new Set($models.map((m) => m.id));
		const firstValidModel = selectedModels.find((model) => availableModelIds.has(model)) ?? '';
		const nextSelectedModels = [firstValidModel];

		if (JSON.stringify(nextSelectedModels) !== JSON.stringify(selectedModels)) {
			selectedModels = nextSelectedModels;
		}
	}
</script>

<div class="flex flex-col w-full items-start">
	{#each selectedModels as selectedModel, selectedModelIdx}
		<div class="flex w-full max-w-fit">
			<div class="overflow-hidden w-full">
				<div class="max-w-full {($settings?.highContrastMode ?? false) ? 'm-1' : 'mr-1'}">
					<Selector
						id={`${selectedModelIdx}`}
						placeholder={$i18n.t('Select a model')}
						searchEnabled={false}
						items={$models
							.filter((model) => model?.owned_by !== 'arena' && !(model?.info?.meta?.hidden ?? false))
							.map((model) => ({
							value: model.id,
							label: getModelDisplayName(model.name, model.id),
							description: getModelShortDescription(model, $i18n.t),
							model: model
						}))}
						{pinModelHandler}
						bind:value={selectedModel}
					/>
				</div>
			</div>
		</div>
	{/each}
</div>
