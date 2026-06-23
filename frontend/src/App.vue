<template>
  <a-config-provider :locale="zhCN">
    <div class="app-layout">
      <header class="app-header">
        <h1>📝 自动阅卷系统</h1>
      </header>
      <main class="app-content">
        <router-view />
      </main>
    </div>
  </a-config-provider>
</template>

<script setup>
import zhCN from 'ant-design-vue/es/locale/zh_CN'
</script>
