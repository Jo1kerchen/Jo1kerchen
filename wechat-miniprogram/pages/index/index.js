const API_BASE = 'https://YOUR_DOMAIN';

Page({
  data: {
    apiBase: API_BASE,
    updatedAt: '',
    cities: [],
    loading: false
  },

  onLoad() {
    this.loadLatest();
  },

  loadLatest() {
    wx.request({
      url: `${API_BASE}/api/latest`,
      method: 'GET',
      success: (res) => {
        const data = res.data || {};
        this.setData({
          updatedAt: data.updated_at || '',
          cities: data.cities || []
        });
      },
      fail: () => {
        wx.showToast({ title: '拉取失败', icon: 'none' });
      }
    });
  },

  refreshData() {
    this.setData({ loading: true });
    wx.request({
      url: `${API_BASE}/api/refresh`,
      method: 'POST',
      success: () => {
        wx.showToast({ title: '刷新成功', icon: 'success' });
        this.loadLatest();
      },
      fail: () => {
        wx.showToast({ title: '刷新失败', icon: 'none' });
      },
      complete: () => {
        this.setData({ loading: false });
      }
    });
  }
});
