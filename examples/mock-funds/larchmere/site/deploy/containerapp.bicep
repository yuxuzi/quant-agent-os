// Larchmere Loan Partners (fictional): the Azure resources the server runs on. Not deployed by
// the tests. Container Apps (consumption), one replica, Log Analytics, Key Vault references, an
// Azure Files share for the run store, and Azure Database for PostgreSQL for the knowledge store.
param location string = resourceGroup().location
param image string = 'larchmereacr.azurecr.io/larchmere-qaos:1'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'larchmere-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 90 }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'larchmere-qaos-mi'
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'larchmere-kv' }

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'larchmere-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'larchmere-qaos'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'http' }
      secrets: [
        { name: 'sql-connection', keyVaultUrl: '${vault.properties.vaultUri}secrets/larchmere-sql-connection', identity: identity.id }
        { name: 'pg-reader', keyVaultUrl: '${vault.properties.vaultUri}secrets/larchmere-pg-reader', identity: identity.id }
        { name: 'shim-secret', keyVaultUrl: '${vault.properties.vaultUri}secrets/larchmere-shim-secret', identity: identity.id }
      ]
    }
    template: {
      // One replica: the run store is a SQLite file on a share, which two writers would corrupt.
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [
        {
          name: 'qaos'
          image: image
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'LARCHMERE_SQL_CONNECTION', secretRef: 'sql-connection' }
            { name: 'PGPASSWORD', secretRef: 'pg-reader' }
            { name: 'LARCHMERE_INTROSPECTION_SECRET', secretRef: 'shim-secret' }
            { name: 'LARCHMERE_LAKE_CATALOG', value: '/lake/catalog.json' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8000 }, periodSeconds: 10 }
          ]
          volumeMounts: [
            { volumeName: 'runs', mountPath: '/data/runs' }
            { volumeName: 'config', mountPath: '/config' }
          ]
        }
      ]
      volumes: [
        { name: 'runs', storageType: 'AzureFile', storageName: 'larchmere-runs' }
        { name: 'config', storageType: 'AzureFile', storageName: 'larchmere-config' }
      ]
    }
  }
}

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: 'larchmere-pg'
  location: location
  sku: { name: 'Standard_B2s', tier: 'Burstable' }
  properties: {
    version: '16'
    storage: { storageSizeGB: 32 }
    authConfig: { activeDirectoryAuth: 'Enabled', passwordAuth: 'Enabled' }
  }
}
// The pgvector extension must be allow-listed (azure.extensions = VECTOR) before `CREATE EXTENSION`.
