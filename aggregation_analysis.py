import numpy as np
import MDAnalysis as mda
import argparse 
import time
import pickle
from sklearn.cluster import DBSCAN
from tqdm import tqdm
from cellgrid import capped_distance_array
from scipy.sparse import csr_matrix

parser = argparse.ArgumentParser(description='Tool for performing aggregation analysis based on DBSCAN for GROMACS MD-trajectories.')
parser.add_argument('-s'         , dest = 'tpr_file'   , type = str   , help = 'name of the .tpr file')
parser.add_argument('-f'         , dest = 'traj_file'  , type = str   , help = 'name of trajectory file (.xtc, .trr, ...)')
parser.add_argument('-res'       , dest = 'residues'   , type = str   , help = 'name of residues to use for aggregation analysis.'  ,nargs='*')
parser.add_argument('-min'       , dest = 'min_samples', type = int   , help = 'minimum number of samples around point to be considered core')
parser.add_argument('-cut_off'   , dest = 'cut_off'    , type = float , help = 'cut-off to use for distances and clustering')
parser.add_argument('-group'     , dest='group'        , type=str     , help = 'complete residues as mol, res', default='atom')
parser.add_argument('-fast'      , action='store_true'                , help = 'use cell-grid algorithm for speedup')
args = parser.parse_args()

#############################################
### 0. some useful definitions
#############################################
class cluster_trajectory:

      def __init__(self):
          self.frames = []

      def add_frame(self, time_step, labels, core_labels, selection):
          self.frames.append(cluster_group(time_step, labels, core_labels, selection))

class cluster_group:

      def __init__(self,time_step, labels, core_labels, selection):
          self.time_step         = time_step
          self.labels            = labels
          self.core_labels       = core_labels
          self.fringe_labels     = np.invert(np.in1d(np.arange(0,selection.indices.shape[0],1),self.core_labels))
          self.core_atomids      = selection[np.in1d(np.arange(0,selection.indices.shape[0],1),self.core_labels)].indices
          self.fringe_atomids    = selection[self.fringe_labels].indices
          self.cluster_atomids   = []
          self.n_clusters        = len(set(self.labels)) - (1 if -1 in self.labels else 0)
                 
          for cluster_index in set(labels):
              if cluster_index != -1:
                 indices = labels == cluster_index
                 atom_ids = selection[indices].indices
                 self.cluster_atomids.append([atom_ids])
                 

      def select_atoms(self, universe, complete=None):
          cluster_atoms = []
          selector = complete
          for cluster_indices in self.cluster_atomids:
              atoms_raw = universe.atoms[np.in1d(u.atoms.indices,cluster_indices)]
              if selector != None:
                 cluster_selection = list(set(list(getattr(atoms_raw,selector)[0])))
                 cluster_atoms.append(universe.atoms[np.in1d(getattr(u.atoms,selector),cluster_selection)])
              else:
                 cluster_atoms.append(atoms_raw)
          return(cluster_atoms)        

      def select_special(self,universe, kind):
          return(universe.atoms[np.in1d(universe.atoms.indices,getattr(self,kind))])  

      def cluster_sizes(self, universe, complete=None):
          cluster_sizes = []
          selector = complete
          for cluster_indices in self.cluster_atomids:
              atoms = universe.atoms[cluster_indices]
              if selector != None:
                 elements = len(list(set(list(getattr(atoms,selector)[0]))))
              else:
                 elements = atoms.positions.shape[1]
              cluster_sizes.append(elements)
          return(cluster_sizes)

def radius_of_gyration(atomgroup):
     # the atom group has a default radius of gyration but we deal here with 
     # CG masses which may or may not give the correct CoM
     positions = atomgroup.positions
     matrix = mda.lib.distances.distance_array(positions,positions, box=u.dimensions)
     return((sum(np.concatenate(np.square(matrix)))/(2*matrix.shape[0])**2.0)**(1/2)/10)


#############################################
### 1. import files 
#############################################

u = mda.Universe(args.tpr_file, args.traj_file)
print("go here")
# the selection is made for residues given via -res option 
selection_command = ""

for res_group in args.residues[:-1]:
    selection_command = selection_command + "resname " + res_group + " or "
selection_command = selection_command + "resname " + args.residues[-1]


############################################
### 2. loop over trajectory
############################################

n_clusters = np.zeros(len(u.trajectory))
traj = cluster_trajectory() 

for ts in tqdm(u.trajectory):
  
    # 1. select the relevant atoms
    selection = u.select_atoms(selection_command)
  
    # 2. compute the sparese distance matrix
   
    if args.fast:
      # !!! The cut-off is not exact but only approximate see: https://github.com/MDAnalysis/cellgrid/issues/12 !!!
      distances = capped_distance_array(selection.positions,selection.positions, args.cut_off, u.dimensions[0:3])
      dist_matrix = csr_matrix((distances[1], (distances[0][:,0],distances[0][:,1])))
    else:
      dist_matrix = mda.lib.distances.distance_array(selection.positions, selection.positions,box=u.dimensions)

    # 3. perform a DBSCAN based clustering
    db = DBSCAN(eps=args.cut_off, min_samples=args.min_samples, metric='precomputed').fit(dist_matrix)
    labels = db.labels_
    core_labels = db.core_sample_indices_
 #   print(core_labels.shape) 

    # 4. create cluster_frame obejct
    # !!! This if statement is needed due to a random error occuring when computing the distances 
    #     Sometimes in 1 out of 100 or so a couple of distances are lost for no apparent reason !!!
    if selection.positions.shape[0] == len(labels):
       traj.add_frame(ts.time, labels, core_labels, selection)

# store the cluster object 
with open("clustered_traj.pickle", 'wb') as output:
     pickle.dump(traj, output, -1)


#####################################################
### 3. Save some information
#####################################################

cluster_file = open("n_clusters.dat",'w')

for frame in traj.frames:
    print(frame.time_step,frame.n_clusters)
    cluster_file.write('{:<8.6F} {:<8.6F} {}'.format(frame.time_step,float(frame.n_clusters),'\n'))
cluster_file.close()


