import styles from './RefreshButton.module.css'

/**
 * @param {{ onRefresh: function, loading: boolean, nextRefreshIn: number }} props
 */
export function RefreshButton({ onRefresh, loading, nextRefreshIn }) {
  return (
    <button
      type="button"
      className={styles.btn}
      onClick={onRefresh}
      disabled={loading}
      title={`Авто-обновление через ${nextRefreshIn}с`}
    >
      <span className={`${styles.icon} ${loading ? styles.spin : ''}`}>↻</span>
      <span>{loading ? 'Загрузка…' : 'Обновить'}</span>
      {!loading && (
        <span className={styles.countdown}>{nextRefreshIn}с</span>
      )}
    </button>
  )
}
