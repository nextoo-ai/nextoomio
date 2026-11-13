<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
const version = '0.0.1-dev';
</script>

<template>
  <main>
    <header>
      <h1>{{ t('app.title') }}</h1>
      <p>{{ t('app.subtitle') }}</p>
      <small>{{ t('app.versionLabel', { version }) }}</small>
    </header>
    <section>
      <h2>{{ t('nav.player') }}</h2>
      <p>{{ t('player.ownerIdle') }}</p>
    </section>
  </main>
</template>
