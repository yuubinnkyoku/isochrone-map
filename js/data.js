// ============================================================
// data.js — stations.json の fetch・管理
// ============================================================
(function () {
  'use strict';

  var DataManager = {
    stations: [],
    meta: null,

    load: function () {
      return this._loadStations();
    },

    _loadStations: function () {
      var self = this;
      var dataVersion = (window.CONFIG && CONFIG.dataVersion) || 1;
      return fetch('data/stations.json?v=' + encodeURIComponent(dataVersion), { cache: 'no-cache' })
        .then(function (res) {
          if (!res.ok) throw new Error('stations.json: ' + res.status);
          return res.json();
        })
        .then(function (data) {
          self.meta = data.meta;
          self.stations = data.stations;
        });
    },

    getMajorCount: function () {
      return this.stations.filter(function (s) { return s.major; }).length;
    },

    getTravelMinutes: function (station) {
      if (station.duration !== undefined) return station.duration;
      if (!this.meta) return 0;
      return this.meta.targetMinutes - station.minutes;
    },
  };

  window.DataManager = DataManager;
})();
