import numpy as np
import glob
import sys
import os
import traceback
import argparse
from collections import OrderedDict

from astropy.coordinates import SkyCoord
import astropy.io.fits as pyfits
from astropy.table import Table, Column
from haloanalysis.utils import collect_dirs
from haloanalysis.batch import *

def extract_sources(tab_srcs):

    # Add all sources
    srcs_delete = []

    skydir = SkyCoord(tab_srcs['ra'],tab_srcs['dec'],unit='deg',
                      frame='icrs')

    srcs = []

    for k, v in fit_data[-1]['sources'].items():

        if v['name'] == 'galdiff':
            continue
        if v['name'] == 'isodiff':
            continue

        skydir0 = SkyCoord(v['ra'],v['dec'],unit='deg',
                           frame='icrs')

        sep = skydir0.separation(skydir).deg

        row_dict_src = {}
        # Look for source of the same name
        m = (tab_srcs['name'] == v['name'])

        if np.sum(m):

            mrow = tab_srcs[m][0]

            print 'match name ', v['name'], mrow['name'], np.min(sep)

            if v['offset'] < mrow['offset']:
                srcs_delete += [np.argmax(m)]
            else:
                continue
        elif len(sep) and np.min(sep) < 0.1:

            mrow = tab_srcs[np.argmin(sep)]

            print 'match sep ', v['name'], mrow['name'], np.min(sep)

            if v['offset'] < mrow['offset']:
                srcs_delete += [np.argmin(sep)]
            else:
                continue

        row_dict_src['name'] = v['name']

        if 'assoc' in v and v['assoc']:
            row_dict_src['assoc'] = v['assoc']['ASSOC1']
        else:
            row_dict_src['assoc'] = ''

        if v['class'] is None or v['class'] == '':
            row_dict_src['class'] = 'unkn'
        else:
            row_dict_src['class'] = v['class']

        row_dict_src['ra'] = v['ra']
        row_dict_src['dec'] = v['dec']
        row_dict_src['glon'] = v['glon']
        row_dict_src['glat'] = v['glat']
        row_dict_src['ts'] = v['ts']
        row_dict_src['npred'] = v['npred']
        row_dict_src['offset'] = v['offset']
        row_dict_src['offset_roi'] = max(np.abs(v['offset_glon']),np.abs(v['offset_glat']))

        if '3FGL' in v['name']:
            row_dict_src['in3fgl'] = 1
        else:
            row_dict_src['in3fgl'] = 0

        srcs += [row_dict_src]

    # remove source pairs
    print 'Deleting rows ', srcs_delete
    tab_srcs.remove_rows(srcs_delete)

    for row in srcs:
        tab_srcs.add_row([row[k] for k in cols_dict_srcs.keys()])

def extract_halo_sed(halo_data,halo_scan_shape):

    o = dict(dlnl = [],
             dlnl_eflux = [])

    if len(halo_data) == 0:
        return {}
    
    for hd in halo_data:

        eflux_scan = hd['sed']['norm_scan']*hd['sed']['ref_eflux'][:,np.newaxis]    
        o['dlnl'] += [hd['sed']['dloglike_scan']]
        o['dlnl_eflux'] += [eflux_scan]

    o['dlnl'] = np.stack(o['dlnl'])
    o['dlnl_eflux'] = np.stack(o['dlnl_eflux'])        
    return o

def extract_halo_data(halo_data, halo_scan_shape):

    o = dict(index = [],
             width = [],
             ts = [],
             eflux = [],
             dlnl = [],
             dlnl_eflux = [])

    if len(halo_data) == 0:
        return {}
    
    for hd in halo_data:

        o['index'] += [np.abs(hd['params']['Index'][0])]
        o['width'] += [hd['SpatialWidth']]
        o['ts'] += [hd['ts']]
        
        o['eflux'] += [hd['eflux_ul95']]
        o['dlnl'] += list(hd['lnlprofile']['dloglike'])
        o['dlnl_eflux'] += list(hd['lnlprofile']['eflux'])

    for k, v in o.items():
        o[k] = np.array(o[k])

    o['width'] = o['width'].reshape(halo_scan_shape)
    o['index'] = o['index'].reshape(halo_scan_shape)
        
    o['ts'] = o['ts'].reshape(halo_scan_shape)
    o['eflux'] = o['eflux'].reshape(halo_scan_shape)
    o['dlnl'] = o['dlnl'].reshape(halo_scan_shape + (9,))
    o['dlnl_eflux'] = o['dlnl_eflux'].reshape(halo_scan_shape + (9,))
        
    return o

def centroid(x,y):

    return np.sum(x)/len(x), np.sum(y)/len(y)

def mean_separation(x,y,xc,yc):

    return np.sum(((x-xc)**2+(y-yc)**2)/len(x))**0.5

def find_source(data):

    srcs = [s for s in data['sources'].values() if np.isfinite(s['offset'])]        
    srcs = sorted(srcs, key=lambda t: float(t['offset']))
    return srcs[0]


def find_new_sources(data):

    srcs = sorted(data['sources'].values(), key=lambda t: t['offset'])

    new_srcs = []
    
    for s in srcs:
        if s['SourceType'] == 'DiffuseSource':
            continue

        if s['offset'] > 1.0:
            continue

        if '3FGL' in s['name']:
            continue
        
        new_srcs += [s]

    return new_srcs


def main():

    usage = "usage: %(prog)s"
    description = "Aggregate analysis output."
    parser = argparse.ArgumentParser(usage=usage,description=description)


    parser.add_argument('--output', default = 'test.fits')
    parser.add_argument('--suffix', default = '')


    parser.add_argument('dirs', nargs='+', default = None,
                        help='Run analyses in all subdirectories of this '
                        'directory.')

    args = parser.parse_args()
    dirs = sorted(collect_dirs(args.dirs))

    nfit = 6
    nscan_pts = 9
    nebins = 20
    halo_scan_shape = (13,9)
    eflux_scan_pts = np.logspace(-9,-5,41)
    
    cols_dict_srcs = OrderedDict()
    cols_dict_srcs['name'] = dict(dtype='S20', format='%s',description='Source Name')
    cols_dict_srcs['assoc'] = dict(dtype='S20', format='%s')
    cols_dict_srcs['class'] = dict(dtype='S20', format='%s')
    cols_dict_srcs['ra'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict_srcs['dec'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict_srcs['glon'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict_srcs['glat'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict_srcs['ts'] = dict(dtype='f8', format='%.2f')
    cols_dict_srcs['npred'] = dict(dtype='f8', format='%.2f')
    cols_dict_srcs['offset'] = dict(dtype='f8', format='%.2f')
    cols_dict_srcs['offset_roi'] = dict(dtype='f8', format='%.2f')
    cols_dict_srcs['in3fgl'] = dict(dtype='i8', format='%i')    
    
    cols_dict_lnl = OrderedDict()
    cols_dict_lnl['name'] = dict(dtype='S20', format='%s',description='Source Name')

    cols_dict_lnl['fit_ext_scan_dlnl'] = dict(dtype='f8', format='%.3f',shape=(27))
    cols_dict_lnl['fit1_ext_scan_dlnl'] = dict(dtype='f8', format='%.3f',shape=(27))

    cols_dict_lnl['fit_halo_scan_dlnl'] = dict(dtype='f8', format='%.3f',
                                               shape=halo_scan_shape + (len(eflux_scan_pts),))
    cols_dict_lnl['fit1_halo_scan_dlnl'] = dict(dtype='f8', format='%.3f',
                                                shape=halo_scan_shape + (len(eflux_scan_pts),))
    
    cols_dict_lnl['fit_halo_sed_scan_dlnl'] = dict(dtype='f8', format='%.3f',
                                                   shape=(halo_scan_shape[0],nebins,len(eflux_scan_pts)))

    
    cols_dict_lnl['fit_src_sed_scan_dlnl'] = dict(dtype='f8', format='%.3f',
                                                   shape=(nebins,nscan_pts))
    cols_dict_lnl['fit_src_sed_scan_eflux'] = dict(dtype='f8', format='%.4g',
                                                   shape=(nebins,nscan_pts))
    
    cols_dict = OrderedDict()
    cols_dict['name'] = dict(dtype='S20', format='%s',description='Source Name')
    cols_dict['codename'] = dict(dtype='S20', format='%s')
    cols_dict['linkname'] = dict(dtype='S20', format='%s')
    cols_dict['assoc'] = dict(dtype='S20', format='%s')
    cols_dict['class'] = dict(dtype='S20', format='%s')
    cols_dict['ra'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict['dec'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict['glon'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict['glat'] = dict(dtype='f8', format='%.3f',unit='deg')
    cols_dict['ts'] = dict(dtype='f8', format='%.2f')
    cols_dict['npred'] = dict(dtype='f8', format='%.2f')
    cols_dict['fitn_ts'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fit_ts'] = dict(dtype='f8', format='%.2f')
    cols_dict['fitn_offset'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fit_offset'] = dict(dtype='f8', format='%.2f')

    cols_dict['dfde1000'] = dict(dtype='f8', format='%.4g')
    cols_dict['dfde1000_err'] = dict(dtype='f8', format='%.4g')
    cols_dict['dfde1000_index'] = dict(dtype='f8', format='%.2f')
    cols_dict['dfde1000_index_err'] = dict(dtype='f8', format='%.2f')
    cols_dict['flux1000'] = dict(dtype='f8', format='%.4g')
    cols_dict['flux1000_err'] = dict(dtype='f8', format='%.4g')
    cols_dict['eflux1000'] = dict(dtype='f8', format='%.4g')
    cols_dict['eflux1000_err'] = dict(dtype='f8', format='%.4g')
    cols_dict['dfde100'] = dict(dtype='f8', format='%.4g')
    cols_dict['dfde100_err'] = dict(dtype='f8', format='%.4g')
    cols_dict['flux100'] = dict(dtype='f8', format='%.4g')
    cols_dict['flux100_err'] = dict(dtype='f8', format='%.4g')
    cols_dict['eflux100'] = dict(dtype='f8', format='%.4g')
    cols_dict['eflux100_err'] = dict(dtype='f8', format='%.4g')

    cols_dict['spectrum_type'] = dict(dtype='S20', format='%s')

    cols_dict['fitn_halo_ts'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_halo_eflux_ul95'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_halo_width'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_halo_index'] = dict(dtype='f8', format='%.2f',shape=(nfit,))

    cols_dict['fit_halo_ts'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_halo_eflux_ul95'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_halo_width'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_halo_index'] = dict(dtype='f8', format='%.2f')

    cols_dict['fitn_ext_ts'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_ext_mle'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_ext_err'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_ext_ul95'] = dict(dtype='f8', format='%.2f',shape=(nfit,))

    cols_dict['fit_ext_ts'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_ext_mle'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_ext_err'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_ext_ul95'] = dict(dtype='f8', format='%.2f')

    cols_dict['fitn_dlike'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_dlike_ext'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_dlike_halo'] = dict(dtype='f8', format='%.2f',shape=(nfit,))

    cols_dict['fit_dlike'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_dlike_ext'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_dlike_halo'] = dict(dtype='f8', format='%.2f')

    cols_dict['fitn_dlike1'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_dlike1_ext'] = dict(dtype='f8', format='%.2f',shape=(nfit,))
    cols_dict['fitn_dlike1_halo'] = dict(dtype='f8', format='%.2f',shape=(nfit,))

    cols_dict['fit_dlike1'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_dlike1_ext'] = dict(dtype='f8', format='%.2f')
    cols_dict['fit_dlike1_halo'] = dict(dtype='f8', format='%.2f')

    cols_dict['fit1_halo_scan_ts'] = dict(dtype='f8', format='%.2f',shape=halo_scan_shape)
    cols_dict['fit1_halo_scan_eflux_ul95'] = dict(dtype='f8', format='%.4g',shape=halo_scan_shape)

    cols_dict['fit_halo_scan_ts'] = dict(dtype='f8', format='%.2f',shape=halo_scan_shape)
    cols_dict['fit_halo_scan_eflux_ul95'] = dict(dtype='f8', format='%.4g',shape=halo_scan_shape)

    cols_dict['fit_nsrc'] = dict(dtype='i8')
    cols_dict['fit_mean_sep'] = dict(dtype='f8', format='%.3f')

    cols = [Column(name=k, **v) for k,v in cols_dict.items()]
    cols_lnl = [Column(name=k, **v) for k,v in cols_dict_lnl.items()]
    cols_srcs = [Column(name=k, **v) for k,v in cols_dict_srcs.items()]

    tab = Table(cols)
    tab_lnl = Table(cols_lnl)
    tab_srcs = Table(cols_srcs)

    row_dict = {}
    row_dict_lnl = {}
    row_dict_srcs = {}

    halo_name = 'halo_RadialGaussian'
    
    for d in dirs:

        logfile = os.path.join(d,'run-halo-analysis.log')

        if not os.path.isfile(logfile):
            continue

        if not check_log(logfile)=='Successful':
            continue

        print d

        fit_data = []
        halo_data = []
        halo_data_sed = []
        halo_fit = []
        new_src_data = []

        if not os.path.isfile(os.path.join(d,'new_source_data.npy')):
            print 'skipping'
            continue

        new_src_data = np.load(os.path.join(d,'new_source_data.npy'))

        for i in range(nfit):

            file1 = os.path.join(d,'fit%i%s.npy'%(i,args.suffix))
            file2 = os.path.join(d,'fit%i%s_%s_data.npy'%(i,args.suffix,halo_name))
            file3 = os.path.join(d,'fit%i%s_%s.npy'%(i,args.suffix,halo_name))
            file4 = os.path.join(d,'fit%i%s_%s_data_idx_free.npy'%(i,args.suffix,halo_name))
            
            if os.path.isfile(file1):
                fit_data += [np.load(file1).flat[0]]

            if os.path.isfile(file2):
                halo_data += [np.load(file2)]

            if os.path.isfile(file4):
                halo_data_sed += [np.load(file4)]

            if os.path.isfile(file3):
                halo_fit += [np.load(file3).flat[0]]

        if len(fit_data) == 0:
            print 'skipping'
            continue
        
        src = find_source(fit_data[0])
        src_name = src['name']

        src_data = []
        ext_data = []
        new_srcs = []

        src_ts = [np.nan]*nfit
        src_offset = [np.nan]*nfit
        fit_dlike = [np.nan]*nfit
        fit_dlike_ext = [np.nan]*nfit
        fit_dlike_halo = [np.nan]*nfit

        fit_dlike1 = [np.nan]*nfit
        fit_dlike1_ext = [np.nan]*nfit
        fit_dlike1_halo = [np.nan]*nfit

        extn_ts = [np.nan]*nfit
        extn_mle = [np.nan]*nfit
        extn_err = [np.nan]*nfit
        extn_ul95 = [np.nan]*nfit

        halon_ts = [np.nan]*nfit
        halon_width = [np.nan]*nfit
        halon_index = [np.nan]*nfit
        halon_eflux_ul95 = [np.nan]*nfit

        halo_pars = []
        for hd in halo_data:
            halo_pars += [extract_halo_data(hd,halo_scan_shape)]

        halo_seds = []
        for hd in halo_data_sed:
            halo_seds += [extract_halo_sed(hd,halo_scan_shape)]
            
        ext_width = []
            
        for i, fd in enumerate(fit_data):

            src = fd['sources'][src_name]
            ext = src['extension']
            src_data += [src]
            ext_data += [src['extension']]

            if src['extension'] is not None:
                extn_ts[i] = max(ext['ts_ext'],0)
                extn_mle[i] = ext['ext']
                extn_err[i] = ext['ext_err']
                extn_ul95[i] = ext['ext_ul95']
                ext_width += [ext['width']]

        for i, fd in enumerate(halo_fit):
            halon_ts[i+1] = fd['sources'][halo_name]['ts']
            halon_eflux_ul95[i+1] = fd['sources'][halo_name]['eflux_ul95']
            halon_width[i+1] = fd['sources'][halo_name]['SpatialWidth']
            halon_index[i+1] = np.abs(fd['sources'][halo_name]['params']['Index'][0])

        for i in range(len(src_data)):
            src_ts[i] = src_data[i]['ts']
            src_offset[i] = src_data[i]['offset']

        fit_nsrc = len(fit_data)-1
        for i in range(len(fit_data)):

            loglike = fit_data[i]['roi']['loglike']
            loglike1 = fit_data[1]['roi']['loglike']

            if i >= 1:
                fit_dlike[i] = loglike - fit_data[i-1]['roi']['loglike']
                fit_dlike1[i] = loglike - loglike1
                fit_dlike1_ext[i] = (loglike+extn_ts[i]/2.) - loglike1
                fit_dlike1_halo[i] = (loglike+halon_ts[i]/2.) - loglike1

            if i >= 1 and i < len(fit_data)-1:
                fit_dlike_ext[i+1] = (fit_data[i]['roi']['loglike']+extn_ts[i]/2.) - fit_data[i+1]['roi']['loglike'] 
                fit_dlike_halo[i+1] = (fit_data[i]['roi']['loglike']+halon_ts[i]/2.) - fit_data[i+1]['roi']['loglike']

        x = [src_data[-1]['offset_glon']]
        y = [src_data[-1]['offset_glat']]

        for j in range(len(new_src_data)):
            x += [new_src_data[j]['offset_glon']]
            y += [new_src_data[j]['offset_glat']]

        xc, yc = centroid(x,y)
        fit_mean_sep = mean_separation(x,y,xc,yc)

        if fit_nsrc == 1:
            fit_mean_sep = np.nan

        codename = os.path.basename(d)
        linkname = '{%s}'%codename.replace('+','p').replace('.','_')

        row_dict['fit1_halo_scan_ts'] = np.array(halo_pars[0]['ts']).reshape(halo_scan_shape)
        row_dict['fit1_halo_scan_eflux_ul95'] = np.array(halo_pars[0]['eflux']).reshape(halo_scan_shape)
        row_dict['fit_halo_scan_ts'] = np.array(halo_pars[-1]['ts']).reshape(halo_scan_shape)
        row_dict['fit_halo_scan_eflux_ul95'] = np.array(halo_pars[-1]['eflux']).reshape(halo_scan_shape)

        row_dict['name'] = src_data[0]['name']
        row_dict['codename'] = codename
        row_dict['linkname'] = linkname
        row_dict['assoc'] = src_data[0]['assoc']['ASSOC1']
        row_dict['class'] = src_data[0]['class']
        row_dict['ra'] = src_data[1]['ra']
        row_dict['dec'] = src_data[1]['dec']
        row_dict['glon'] = src_data[1]['glon']
        row_dict['glat'] = src_data[1]['glat']
        row_dict['ts'] = src_data[1]['ts']
        row_dict['npred'] = src_data[1]['npred']
        row_dict['fitn_ts'] = src_ts
        row_dict['fit_ts'] = src_data[-1]['ts']
        row_dict['fitn_offset'] = src_offset
        row_dict['fit_offset'] = src_data[-1]['offset']

        for t in ['dfde','flux','eflux']:    
            row_dict['%s1000'%t] = src_data[1]['%s1000'%t][0]
            row_dict['%s1000_err'%t] = src_data[1]['%s1000'%t][1]
            row_dict['%s100'%t] = src_data[1]['%s100'%t][0]
            row_dict['%s100_err'%t] = src_data[1]['%s100'%t][1]

        row_dict['dfde1000_index'] = src_data[1]['dfde1000_index'][0]
        row_dict['dfde1000_index_err'] = src_data[1]['dfde1000_index'][1]
        row_dict['spectrum_type'] = src_data[1]['SpectrumType']

        row_dict['fitn_dlike'] = fit_dlike
        row_dict['fitn_dlike_ext'] = fit_dlike_ext
        row_dict['fitn_dlike_halo'] = fit_dlike_halo
        row_dict['fit_dlike'] = fit_dlike[fit_nsrc]
        row_dict['fit_dlike_ext'] = fit_dlike_ext[fit_nsrc]
        row_dict['fit_dlike_halo'] = fit_dlike_halo[fit_nsrc]

        row_dict['fitn_dlike1'] = fit_dlike1
        row_dict['fitn_dlike1_ext'] = fit_dlike1_ext
        row_dict['fitn_dlike1_halo'] = fit_dlike1_halo
        row_dict['fit_dlike1'] = fit_dlike1[fit_nsrc]
        row_dict['fit_dlike1_ext'] = fit_dlike1_ext[fit_nsrc]
        row_dict['fit_dlike1_halo'] = fit_dlike1_halo[fit_nsrc]

        row_dict['fit_mean_sep'] = fit_mean_sep
        row_dict['fit_nsrc'] = fit_nsrc    

        row_dict['fitn_halo_ts'] = halon_ts
        row_dict['fitn_halo_width'] = halon_width
        row_dict['fitn_halo_index'] = halon_index
        row_dict['fitn_halo_eflux_ul95'] = halon_eflux_ul95
        row_dict['fit_halo_ts'] = halon_ts[fit_nsrc]
        row_dict['fit_halo_width'] = halon_width[fit_nsrc]
        row_dict['fit_halo_index'] = halon_index[fit_nsrc]
        row_dict['fit_halo_eflux_ul95'] = halon_eflux_ul95[fit_nsrc]

        row_dict['fitn_ext_ts'] = extn_ts
        row_dict['fitn_ext_mle'] = extn_mle
        row_dict['fitn_ext_err'] = extn_err
        row_dict['fitn_ext_ul95'] = extn_ul95
        row_dict['fit_ext_ts'] = extn_ts[fit_nsrc]
        row_dict['fit_ext_mle'] = extn_mle[fit_nsrc]
        row_dict['fit_ext_err'] = extn_err[fit_nsrc]
        row_dict['fit_ext_ul95'] = extn_ul95[fit_nsrc]    

        row = []
        for k in cols_dict.keys():
            row += [row_dict[k]]
        tab.add_row(row)
        
        from scipy.interpolate import UnivariateSpline

        fit1_dlnl_interp = np.zeros(halo_scan_shape + (len(eflux_scan_pts),))
        fit_dlnl_interp = np.zeros(halo_scan_shape + (len(eflux_scan_pts),))

        fit_sed_dlnl_interp = np.zeros((halo_scan_shape[0],nebins,len(eflux_scan_pts)))
                
        for i in range(halo_scan_shape[0]):

            for j in range(nebins):
                sp = UnivariateSpline(halo_seds[-1]['dlnl_eflux'][i,j],
                                      halo_seds[-1]['dlnl'][i,j],k=2,s=0.001)
                fit_sed_dlnl_interp[i,j] = sp(eflux_scan_pts)
                
            
            for j in range(halo_scan_shape[1]):
                sp = UnivariateSpline(halo_pars[0]['dlnl_eflux'][i,j],halo_pars[0]['dlnl'][i,j],k=2,s=0.001)
                fit1_dlnl_interp[i,j] = sp(eflux_scan_pts)

                sp = UnivariateSpline(halo_pars[-1]['dlnl_eflux'][i,j],halo_pars[-1]['dlnl'][i,j],k=2,s=0.001)
                fit_dlnl_interp[i,j] = sp(eflux_scan_pts)

        row_dict_lnl['fit_src_sed_scan_dlnl'] = np.zeros((nebins,nscan_pts))
        row_dict_lnl['fit_src_sed_scan_eflux'] = np.zeros((nebins,nscan_pts))
                
        for i in range(nebins):
            row_dict_lnl['fit_src_sed_scan_dlnl'][i,:] = src_data[-1]['sed']['lnlprofile'][i]['dloglike']
            row_dict_lnl['fit_src_sed_scan_eflux'][i,:] = src_data[-1]['sed']['lnlprofile'][i]['eflux']
#            row_dict_lnl['fit_src_sed_scan_dlnl']

        row_dict_lnl['name'] = src_data[0]['name']
        row_dict_lnl['fit1_ext_scan_dlnl'] = ext_data[1]['dloglike']
        row_dict_lnl['fit_ext_scan_dlnl'] = ext_data[-1]['dloglike']

        row_dict_lnl['fit1_halo_scan_dlnl'] = fit1_dlnl_interp
        row_dict_lnl['fit_halo_scan_dlnl'] = fit_dlnl_interp

        row_dict_lnl['fit_halo_sed_scan_dlnl'] = fit_sed_dlnl_interp
        
        row = []
        for k in cols_dict_lnl.keys():
            row += [row_dict_lnl[k]]
        tab_lnl.add_row(row)

    m = tab['class']==''
    tab['class'][m] = 'unkn'

    output_lnl = os.path.splitext(args.output)[0] + '_lnl.fits'
    output_srcs = os.path.splitext(args.output)[0] + '_srcs.fits'

    tab.write(args.output,format='fits',overwrite=True)
    tab_lnl.write(output_lnl,format='fits',overwrite=True)
    #tab_srcs.write(output_srcs,format='fits',overwrite=True)

    col1 = pyfits.Column(name='halo_scan_width', format='D',
                         array=halo_pars[0]['width'][:,0])
    col2 = pyfits.Column(name='halo_scan_index', format='D',
                         array=halo_pars[0]['index'][0,:])
    col3 = pyfits.Column(name='halo_scan_eflux', format='D',
                         array=eflux_scan_pts)
    col4 = pyfits.Column(name='ext_width', format='D', array=ext_width[0])
    
    tbhdu1 = pyfits.BinTableHDU.from_columns([col1],name='halo_scan_width')
    tbhdu2 = pyfits.BinTableHDU.from_columns([col2],name='halo_scan_index')
    tbhdu3 = pyfits.BinTableHDU.from_columns([col3],name='halo_scan_eflux')
    tbhdu4 = pyfits.BinTableHDU.from_columns([col4],name='ext_width')

    hdulist = pyfits.open(args.output)
    
    hdulist.append(tbhdu1)
    hdulist.append(tbhdu2)
    hdulist.append(tbhdu3)
    hdulist.append(tbhdu4)
    hdulist.writeto(args.output,clobber=True)


    hdulist = pyfits.open(output_lnl)
    hdulist.append(tbhdu1)
    hdulist.append(tbhdu2)
    hdulist.writeto(output_lnl,clobber=True)


if __name__ == '__main__':

    main()